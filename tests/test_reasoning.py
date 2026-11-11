import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.conversation.models import CancellationToken, Message
from backend.inference.base import InferenceRequest
from backend.intelligence.reasoning import (
    ContextBuilder, IntentCategory, IntentClassifier, ModelBackedUnderstanding,
    PlanStep, ReasoningEngine, TaskIntent, TaskOrchestrator, TaskPlan,
    TaskStatus, TaskStore, VerificationStatus,
)
from backend.permissions.policy import PermissionLevel, PermissionPolicy
from backend.security.audit import AuditLogger
from backend.tools.registry import ToolRegistry, ToolSpec


class FakeProvider:
    def __init__(self, text='{}', available=True): self.text, self.is_available = text, available
    def available(self): return self.is_available
    def stream(self, request, cancellation): yield self.text


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = PermissionPolicy(allowed_tools=frozenset({'ok', 'restricted', 'slow', 'slow_cancel', 'empty'}))
        self.audit = AuditLogger(Path(self.temp.name) / 'audit.log')
        self.tools = ToolRegistry(self.policy, self.audit)
        self.addCleanup(self.tools.shutdown)
        self.tools.register(ToolSpec('ok', 'safe test tool', {'type': 'object'}, {'type': 'object'},
                                     PermissionLevel.READ_ONLY, 1, lambda args: {'value': 5}))
        self.tools.register(ToolSpec('restricted', 'not allowed by level', {'type': 'object'}, {'type': 'object'},
                                     PermissionLevel.WRITE_LOCAL, 1, lambda args: {'changed': True}))

    def test_intent_categories_objectives_and_constraints(self):
        classifier = IntentClassifier()
        self.assertEqual(classifier.classify('Please debug this Python test').category, IntentCategory.CODING)
        intent = classifier.classify('Analyze the CSV, then compare results without using network')
        self.assertEqual(intent.category, IntentCategory.MULTI_STEP)
        self.assertEqual(intent.objective, 'Analyze the CSV, then compare results without using network')
        self.assertTrue(intent.constraints)
        self.assertEqual(intent.source, 'deterministic-fallback')

    def test_model_backed_parse_and_invalid_falls_back(self):
        payload = json.dumps({'category': 'coding', 'objective': 'fix bug', 'subtasks': ['inspect', 'fix'],
                              'constraints': ['no network'], 'required_information': [], 'expected_output': 'patch',
                              'risk_level': 'low', 'required_permissions': [], 'confidence': .8})
        understood = ModelBackedUnderstanding(FakeProvider(payload)).understand('fix bug', (Message('user', 'fix bug'),))
        self.assertEqual(understood.source, 'model')
        self.assertEqual(understood.category, IntentCategory.CODING)
        fallback = ModelBackedUnderstanding(FakeProvider(available=False)).understand('hello', ())
        self.assertEqual(fallback.source, 'deterministic-fallback')

    def test_context_is_bounded_and_uses_only_supplied_memory(self):
        builder = ContextBuilder(max_characters=200, max_history=2)
        messages = builder.build('current request', recent=(Message('user', 'old' * 50),),
                                 memories=('approved memory',), tools=({'name': 'ok'},))
        self.assertTrue(all(len(message.content) <= 200 for message in messages))
        self.assertIn('approved memory', messages[0].content)
        self.assertEqual(messages[-1].content, 'current request')

    def test_plan_validates_registered_tool_and_dependency_order(self):
        orchestrator = TaskOrchestrator(self.tools)
        bad = TaskPlan('t', 'objective', 'desc', (PlanStep('a', 'first', dependencies=('b',)), PlanStep('b', 'later')))
        with self.assertRaises(ValueError): orchestrator.validate(bad)
        unknown = TaskPlan('u', 'objective', 'desc', (PlanStep('a', 'run', tool='shell'),))
        with self.assertRaises(ValueError): orchestrator.validate(unknown)

    def test_executes_and_verifies_registered_read_only_tool(self):
        plan = TaskPlan('t', 'status', 'status', (PlanStep('s', 'read status', 'ok', verification=('output_has_keys',)),))
        result = TaskOrchestrator(self.tools).execute(plan)
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertEqual(result.verification, VerificationStatus.SUCCESS)

    def test_permission_enforcement_and_no_implicit_retry_for_write_tool(self):
        step = PlanStep('s', 'write', 'restricted', retries=1)
        plan = TaskPlan('t', 'write', 'write', (step,))
        with self.assertRaises(ValueError): TaskOrchestrator(self.tools).validate(plan)
        result = self.tools.execute('restricted', {})
        self.assertFalse(result.ok)

    def test_dependency_failure_prevents_downstream_execution(self):
        plan = TaskPlan('t', 'combined', 'combined', (
            PlanStep('a', 'unavailable executor'),
            PlanStep('b', 'depends', 'ok', dependencies=('a',)),
        ))
        result = TaskOrchestrator(self.tools).execute(plan)
        self.assertEqual(result.steps[1].error, 'dependency_failed')

    def test_persistence_round_trip(self):
        store = TaskStore(Path(self.temp.name) / 'tasks.db')
        plan = TaskPlan('persisted', 'objective', 'description', (PlanStep('step-1', 'step'),))
        store.save(plan)
        found = store.get('persisted')
        self.assertEqual(found['plan']['objective'], 'objective')
        self.assertEqual(store.list()[0]['task_id'], 'persisted')

    def test_reasoning_engine_plan_facade_uses_fallback_when_model_unavailable(self):
        engine = ReasoningEngine(FakeProvider(available=False), self.tools)
        intent, plan = engine.plan('What is the system status?')
        self.assertEqual(intent.source, 'deterministic-fallback')
        self.assertEqual(plan.steps[0].tool, 'system.status' if self.tools.select('system.status') else None)

    def test_timeout_retries_are_bounded(self):
        calls = []
        def slow(_):
            calls.append(1)
            time.sleep(.04)
            return {'done': True}
        self.tools.register(ToolSpec('slow', 'slow read', {'type': 'object'}, {'type': 'object'},
                                     PermissionLevel.READ_ONLY, .005, slow))
        plan = TaskPlan('retry', 'read', 'read', (PlanStep('s', 'slow read', 'slow', retries=2),))
        result = TaskOrchestrator(self.tools).execute(plan)
        self.assertEqual(result.steps[0].retries, 2)
        self.assertEqual(result.steps[0].error, 'tool_timeout')
        self.assertLessEqual(len(calls), 3)

    def test_cancellation_stops_before_next_step(self):
        entered = threading.Event()
        def slow(_):
            entered.set()
            time.sleep(.08)
            return {'done': True}
        self.tools.register(ToolSpec('slow_cancel', 'slow read', {'type': 'object'}, {'type': 'object'},
                                     PermissionLevel.READ_ONLY, 1, slow))
        plan = TaskPlan('cancel-me', 'two reads', 'two reads', (
            PlanStep('a', 'first', 'slow_cancel'), PlanStep('b', 'second', 'ok', dependencies=('a',))))
        results = []
        # Use a shared orchestrator so cancellation targets the active task.
        orchestrator = TaskOrchestrator(self.tools)
        thread = threading.Thread(target=lambda: results.append(orchestrator.execute(plan)))
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(orchestrator.cancel('cancel-me'))
        thread.join(2)
        self.assertEqual(results[0].status, TaskStatus.CANCELLED)
        self.assertEqual(len(results[0].steps), 1)

    def test_empty_output_fails_verification(self):
        self.tools.register(ToolSpec('empty', 'empty result', {'type': 'object'}, {'type': 'object'},
                                     PermissionLevel.READ_ONLY, 1, lambda args: {}))
        plan = TaskPlan('verify', 'verify output', 'verify output',
                        (PlanStep('s', 'read', 'empty', verification=('output_has_keys',)),))
        result = TaskOrchestrator(self.tools).execute(plan)
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.steps[0].verification, VerificationStatus.FAILURE)


if __name__ == '__main__': unittest.main()
