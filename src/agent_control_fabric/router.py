"""Concurrent, target-aware action routing."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Optional

from .backends import Backend
from .locks import ResourceLock
from .models import Action, BackendResult
from .retrospect import record_trajectory


class ActionRouter:
    """Serialize each physical target while allowing independent targets to fan out."""

    def __init__(
        self,
        backend: Backend,
        verifier: Any | None = None,
        *,
        max_workers: int = 4,
        use_cross_process_locks: bool = False,
        lock_ttl: float = 120.0,
    ):
        self.backend = backend
        self.verifier = verifier
        self.max_workers = max(1, int(max_workers))
        self.use_cross_process_locks = use_cross_process_locks
        self.lock_ttl = lock_ttl
        self._locks: dict[tuple[Any, ...], Lock] = {}
        self._locks_guard = Lock()

    def _lock_for(self, key: tuple[Any, ...]) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, Lock())

    def execute(self, action: Action) -> BackendResult:
        lock = self._lock_for(action.target.lock_key())
        with lock:
            if self.use_cross_process_locks:
                target_str = f"target_{'_'.join(str(p) for p in action.target.lock_key())}"
                with ResourceLock(target_str, agent=action.agent_id, ttl=self.lock_ttl):
                    return self._dispatch(action)
            return self._dispatch(action)

    def _dispatch(self, action: Action) -> BackendResult:
        t0 = time.perf_counter()
        result = self.backend.execute(action)
        if result.status == "executed" and action.expected:
            if self.verifier is None:
                result.details["verification"] = {
                    "status": "unknown",
                    "reason": "no verifier configured",
                }
            else:
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
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result.details["elapsed_ms"] = round(elapsed_ms, 2)
        record_trajectory(action, result, elapsed_ms)
        return result

    def execute_many(self, actions: list[Action]) -> list[BackendResult]:
        if not actions:
            return []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self.execute, action) for action in actions]
            return [future.result() for future in futures]
