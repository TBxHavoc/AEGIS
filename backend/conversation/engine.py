"""Conversation orchestration with exactly one terminal response per request."""
from __future__ import annotations

import secrets
import logging
from collections.abc import Iterator
from threading import RLock

from backend.conversation.context import ContextAssembler
from backend.conversation.models import CancellationToken, Message, StreamEvent
from backend.conversation.persistence import ConversationStore
from backend.inference.base import InferenceError, InferenceRequest, InferenceProvider

LOG = logging.getLogger(__name__)


class ConversationEngine:
    def __init__(self, provider: InferenceProvider | None, store: ConversationStore | None = None,
                 context: ContextAssembler | None = None, persist: bool = False) -> None:
        self.provider = provider
        self.store = store
        self.context = context or ContextAssembler()
        self.persist = persist
        self._histories: dict[str, list[Message]] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = RLock()

    def history(self, session_id: str) -> list[Message]:
        with self._lock:
            if session_id not in self._histories:
                self._histories[session_id] = self.store.history(session_id) if self.store and self.persist else []
            return list(self._histories[session_id])

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(request_id)
            if token is None:
                return False
            token.cancel()
            return True

    def stream(self, session_id: str, request_id: str, user_message: str, *, memories: tuple[str, ...] = (),
               system_prompt: str | None = None, agent_context: str = "") -> Iterator[StreamEvent]:
        if not user_message.strip():
            yield StreamEvent("conversation.response.failed", request_id, {"error": "invalid_message"})
            return
        token = CancellationToken()
        with self._lock:
            self._tokens[request_id] = token
            history = self.history(session_id)
        user = Message("user", user_message, secrets.token_urlsafe(12))
        with self._lock:
            self._histories.setdefault(session_id, []).append(user)
        if self.persist and self.store:
            self.store.append(session_id, user)
        yield StreamEvent("conversation.response.started", request_id, {"session_id": session_id})
        if token.cancelled:
            yield StreamEvent("conversation.response.failed", request_id, {"error": "cancelled"})
            with self._lock:
                self._tokens.pop(request_id, None)
            return
        if self.provider is None or not self.provider.available():
            yield StreamEvent("conversation.response.failed", request_id, {"error": "model_unavailable"})
            with self._lock:
                self._tokens.pop(request_id, None)
            return
        response_parts: list[str] = []
        try:
            context = self.context.assemble(history, user_message, memories=memories,
                                            system_prompt=system_prompt, agent_context=agent_context)
            request = InferenceRequest(context.messages, request_id)
            for chunk in self.provider.stream(request, token):
                if token.cancelled:
                    yield StreamEvent("conversation.response.failed", request_id, {"error": "cancelled"})
                    return
                response_parts.append(chunk)
                yield StreamEvent("conversation.response.chunk", request_id, {"text": chunk})
            if token.cancelled:
                error = "inference_timeout" if token.reason == "timeout" else "cancelled"
                yield StreamEvent("conversation.response.failed", request_id, {"error": error})
                return
            assistant = Message("assistant", "".join(response_parts), secrets.token_urlsafe(12))
            with self._lock:
                self._histories.setdefault(session_id, []).append(assistant)
            if self.persist and self.store:
                self.store.append(session_id, assistant)
            yield StreamEvent("conversation.response.completed", request_id, {"message_id": assistant.message_id,
                                                                                "text": assistant.content})
        except InferenceError as exc:
            yield StreamEvent("conversation.response.failed", request_id, {"error": str(exc)})
        except Exception:
            LOG.exception("Conversation request failed: %s", request_id)
            yield StreamEvent("conversation.response.failed", request_id, {"error": "conversation_error"})
        finally:
            with self._lock:
                self._tokens.pop(request_id, None)

