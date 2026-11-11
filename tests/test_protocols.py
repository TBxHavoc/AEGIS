from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.intelligence.reasoning import TaskStatus
from backend.permissions.policy import PermissionPolicy
from backend.protocols.models import ProtocolSpec, ProtocolStatus
from backend.protocols.parser import ProtocolDraftParser
from backend.protocols.registry import ProtocolError, ProtocolRegistry
from backend.protocols.resolver import ProtocolResolver
from backend.protocols.validator import validate_protocol
from backend.security.audit import AuditLogger
from backend.tools.registry import ToolRegistry


class ProtocolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.policy = PermissionPolicy(allowed_tools=frozenset({'system.status'}))
        self.audit = AuditLogger(root / 'audit.log')
        self.registry = ProtocolRegistry(root / 'protocols.db', self.audit)

    def _spec(self, protocol_id='p1', **changes):
        base = ProtocolSpec(name='Coding rules', purpose='Verify code changes', scope='software development',
                            category='software_development', protocol_id=protocol_id,
                            rules=('Code changes must be tested.',), procedures=('Run tests', 'Review changes'))
        from dataclasses import replace
        return replace(base, **changes)

    def test_deterministic_parser_example_and_missing_requirements(self):
        parsed = ProtocolDraftParser().parse(
            'Create Protocol 001 for software development. Every code change must be tested, reviewed, documented and verified before acceptance.')
        self.assertEqual(parsed.protocol_id, '001')
        self.assertEqual(parsed.category, 'software_development')
        self.assertEqual(len(parsed.procedures), 4)
        missing = ProtocolDraftParser().parse('Create Protocol 002 for software development')
        self.assertIn('at least one procedure is required', validate_protocol(missing))

    def test_validation_duplicate_ids_and_immutable_revisions(self):
        original = self.registry.create(self._spec())
        self.registry.approve(original.protocol_id)
        self.registry.activate(original.protocol_id)
        with self.assertRaises(ProtocolError): self.registry.create(self._spec())
        revision = self.registry.revise(original.protocol_id, purpose='Updated validation purpose')
        self.assertNotEqual(revision.protocol_id, original.protocol_id)
        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.status, ProtocolStatus.PROPOSED)
        self.assertEqual(self.registry.get(original.protocol_id).purpose, original.purpose)
        self.assertEqual([item.version for item in self.registry.history(original.protocol_id)], [1, 2])
        self.registry.approve(revision.protocol_id)
        self.registry.activate(revision.protocol_id)
        self.assertEqual(self.registry.get(original.protocol_id).status, ProtocolStatus.INACTIVE)
        self.assertEqual(self.registry.get(revision.protocol_id).status, ProtocolStatus.ACTIVE)

    def test_approval_activation_deactivation_archive_and_restore(self):
        spec = self.registry.create(self._spec())
        with self.assertRaises(ProtocolError): self.registry.activate(spec.protocol_id)
        self.assertEqual(self.registry.approve(spec.protocol_id).status, ProtocolStatus.APPROVED)
        self.assertEqual(self.registry.activate(spec.protocol_id).status, ProtocolStatus.ACTIVE)
        self.assertEqual(self.registry.deactivate(spec.protocol_id).status, ProtocolStatus.INACTIVE)
        self.assertEqual(self.registry.archive(spec.protocol_id).status, ProtocolStatus.ARCHIVED)
        self.assertEqual(self.registry.restore(spec.protocol_id).status, ProtocolStatus.INACTIVE)

    def test_unapproved_protocol_is_never_resolved(self):
        draft = self.registry.create(self._spec())
        resolver = ProtocolResolver(self.registry, self.policy)
        self.assertEqual(resolver.resolve('implement a coding change', 'coding').protocols, ())
        self.registry.approve(draft.protocol_id)
        self.assertEqual(resolver.resolve('implement a coding change', 'coding').protocols, ())

    def test_active_protocol_scope_matching_and_priority_conflicts(self):
        high = self.registry.create(self._spec('high', priority=20, rules=('You must run tests.',), triggers=('coding',)))
        low = self.registry.create(self._spec('low', scope='code implementation', priority=10,
                                              rules=('You must not run tests.',), triggers=('coding',)))
        for item in (high, low): self.registry.approve(item.protocol_id); self.registry.activate(item.protocol_id)
        resolved = ProtocolResolver(self.registry, self.policy).resolve('coding change', 'coding')
        self.assertEqual([item.protocol_id for item in resolved.protocols], ['high'])
        self.assertEqual(resolved.overridden, ('low',))
        tied = self.registry.create(self._spec('tied', scope='another coding scope', priority=20,
                                               rules=('Never run tests.',), triggers=('coding',)))
        self.registry.approve(tied.protocol_id); self.registry.activate(tied.protocol_id)
        result = ProtocolResolver(self.registry, self.policy).resolve('coding change', 'coding')
        self.assertTrue(result.conflicts)
        self.assertTrue(result.blocked)

    def test_protocol_permission_cannot_escalate_and_preconditions_block(self):
        elevated = self.registry.create(self._spec('write', required_permissions=('WRITE_LOCAL',)))
        self.registry.approve(elevated.protocol_id); self.registry.activate(elevated.protocol_id)
        resolution = ProtocolResolver(self.registry, self.policy).resolve('coding change', 'coding')
        self.assertIn('exceeds current permission policy', resolution.blocked_reason)
        self.registry.deactivate(elevated.protocol_id)
        conditioned = self.registry.create(self._spec('condition', preconditions=('User approved change',)))
        self.registry.approve(conditioned.protocol_id); self.registry.activate(conditioned.protocol_id)
        resolution = ProtocolResolver(self.registry, self.policy).resolve('coding change', 'coding')
        self.assertIn('preconditions cannot be verified', resolution.blocked_reason)

    def test_registry_persistence_and_compliance_history(self):
        spec = self.registry.create(self._spec())
        reopened = ProtocolRegistry(Path(self.temp.name) / 'protocols.db', self.audit)
        self.assertEqual(reopened.get(spec.protocol_id).name, spec.name)
        reopened.record_compliance('task-1', spec.protocol_id, spec.version, 'PARTIALLY_COMPLIANT', {'done': False})
        rows = reopened.compliance_history(task_id='task-1')
        self.assertEqual(rows[0]['status'], 'PARTIALLY_COMPLIANT')

    def test_invalid_executable_protocol_content_rejected(self):
        invalid = self._spec('bad', procedures=('shell: rm -rf /',))
        with self.assertRaises(ProtocolError): self.registry.create(invalid)

    def test_protocol_mutation_permission_requires_locality_and_confirmation(self):
        from backend.permissions.policy import ConfirmationRequired, PermissionError as PolicyPermissionError
        with self.assertRaises(ConfirmationRequired):
            PermissionPolicy.authorize_protocol_mutation('approve', local_client=True)
        with self.assertRaises(PolicyPermissionError):
            PermissionPolicy.authorize_protocol_mutation('activate', local_client=False, confirmed=True)

    def test_task_planner_applies_active_protocol_and_tracks_compliance(self):
        spec = self.registry.create(self._spec())
        self.registry.approve(spec.protocol_id); self.registry.activate(spec.protocol_id)
        tools = ToolRegistry(self.policy, self.audit)
        self.addCleanup(tools.shutdown)
        from backend.intelligence.reasoning import ReasoningEngine
        class Provider:
            def available(self): return False
            def stream(self, request, token): return iter(())
        from backend.protocols.resolver import ProtocolResolver
        engine = ReasoningEngine(Provider(), tools, protocol_resolver=ProtocolResolver(self.registry, self.policy),
                                 protocol_registry=self.registry)
        intent, plan = engine.plan('Implement a code change')
        self.assertEqual(intent.category.value, 'coding')
        self.assertEqual(plan.protocols[0].version, 1)
        self.assertTrue(any(step.protocol_id == spec.protocol_id for step in plan.steps))
        result = engine.orchestrator.execute(plan)
        self.assertEqual(result.status, TaskStatus.PARTIAL)
        self.assertEqual(result.protocol_compliance[0]['status'], 'PARTIALLY_COMPLIANT')
        self.assertEqual(self.registry.compliance_history(task_id=plan.task_id)[0]['status'], 'PARTIALLY_COMPLIANT')


class ProtocolApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = AegisServer(Settings(port=0, data_dir=cls.temp.name,
            static_dir=str(Path(__file__).parents[1] / 'frontend'), knowledge_root=str(Path(cls.temp.name) / 'knowledge')))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2); cls.temp.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        payload = json.dumps(body) if body is not None else None
        headers = {'Content-Type': 'application/json'} if payload else {}
        headers['Authorization'] = 'Bearer ' + self.server.api_token
        connection.request(method, path, payload, headers)
        response = connection.getresponse(); data = json.loads(response.read())
        connection.close(); return response.status, data

    def test_draft_preview_approval_control_and_versions_api(self):
        status, _ = self.request('POST', '/api/protocols', {
            'name': 'Bad schema', 'purpose': 'test', 'scope': 'test', 'procedures': 'must be an array'})
        self.assertEqual(status, 400)
        instruction = 'Create Protocol 001 for software development. Every code change must be tested, reviewed, documented and verified before acceptance.'
        status, preview = self.request('POST', '/api/protocols/draft/preview', {'instruction': instruction})
        self.assertEqual(status, 200); self.assertEqual(preview['draft']['status'], 'proposed')
        status, created = self.request('POST', '/api/protocols/draft', {'instruction': instruction})
        self.assertEqual(status, 201)
        protocol_id = created['draft']['protocol_id']
        status, _ = self.request('POST', f'/api/protocols/{protocol_id}/approve', {})
        self.assertEqual(status, 403)
        status, approved = self.request('POST', f'/api/protocols/{protocol_id}/approve', {'confirmed': True})
        self.assertEqual(status, 200); self.assertEqual(approved['status'], 'approved')
        status, active = self.request('POST', f'/api/protocols/{protocol_id}/activate', {'confirmed': True})
        self.assertEqual(status, 200); self.assertEqual(active['status'], 'active')
        status, versions = self.request('GET', f'/api/protocols/{protocol_id}/versions')
        self.assertEqual(status, 200); self.assertEqual(len(versions['versions']), 1)

    def test_protocol_creation_intent_creates_only_a_draft(self):
        instruction = 'Create Protocol 009 for research. Research findings must be reviewed and documented before sharing.'
        status, data = self.request('POST', '/api/reasoning/tasks', {'request': instruction})
        self.assertEqual(status, 201)
        self.assertEqual(data['kind'], 'protocol_draft')
        self.assertTrue(data['approval_required'])
        self.assertEqual(data['draft']['status'], 'proposed')


if __name__ == '__main__': unittest.main()
