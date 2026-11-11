"""Bounded in-memory session handling for the foundation release."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass(slots=True)
class Session:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class SessionManager:
    def __init__(self, max_sessions: int = 32) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_sessions = max_sessions
        self._lock = Lock()

    def create(self) -> Session:
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                oldest = min(self._sessions, key=lambda key: self._sessions[key].last_seen)
                del self._sessions[oldest]
            session = Session(secrets.token_urlsafe(18))
            self._sessions[session.session_id] = session
            return session

    def touch(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_seen = time.time()
            return session

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

