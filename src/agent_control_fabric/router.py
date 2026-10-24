"""Concurrent, target-aware action routing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from .backends import Backend
from .models import Action, BackendResult


class ActionRouter:
    """Serialize each physical target while allowing independent targets to fan out."""

    def __init__(self, backend: Backend, verifier: Any | None = None, *, max_workers: int = 4):
        self.backend = backend
        self.verifier = verifier
        self.max_workers = max(1, int(max_workers))
        self._locks: dict[tuple[Any, ...], Lock] = {}
        self._locks_guard = Lock()

    def _lock_for(self, key: tuple[Any, ...]) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, Lock())

    def execute(self, action: Action) -> BackendResult:
        lock = self._lock_for(action.target.lock_key())
        with lock:
            result = self.backend.execute(action)
            if result.status != "executed" or not action.expected:
                return result
            if self.verifier is None:
                result.details["verification"] = {
                    "status": "unknown",
                    "reason": "no verifier configured",
                }
                return result
            verification = self.verifier.verify(action)
            result.details["verification"] = {
                "status": verification.status,
                **verification.details,
            }
            if verification.status == "satisfied":
                result.status = "verified"
            elif verification.status == "unsatisfied":
                result.status = "failed"
            else:
                result.status = "unknown"
            return result

    def execute_many(self, actions: list[Action]) -> list[BackendResult]:
        if not actions:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self.execute, action) for action in actions]
            return [future.result() for future in futures]
