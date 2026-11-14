"""Concurrent, target-aware action routing."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Optional

from .backends import Backend
from .layout import UserInputGuard
from .locks import ResourceLock
from .models import Action, BackendResult
from .policy import check_foreground_allowed, is_no_foreground_enforced
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
        no_foreground: bool = True,
        focus_guard: bool = True,
    ):
        self.backend = backend
        self.verifier = verifier
        self.max_workers = max(1, int(max_workers))
        self.use_cross_process_locks = use_cross_process_locks
        self.lock_ttl = lock_ttl
        self.no_foreground = bool(no_foreground)
        self.focus_guard = bool(focus_guard)
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
        effective_no_fg = self.no_foreground or (is_no_foreground_enforced() is True)
        allowed, reason = check_foreground_allowed(
            action,
            allow_foreground=not effective_no_fg,
            no_foreground=effective_no_fg,
        )
        if not allowed:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result = BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend="router-no-foreground",
                details={
                    "code": "foreground_not_allowed",
                    "message": reason,
                    "no_foreground": True,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
            record_trajectory(action, result, elapsed_ms)
            return result

        use_guard = self.focus_guard and action.kind != "wait" and action.target.kind == "window"
        guard = UserInputGuard(restore_foreground_on_drift=True) if use_guard else None
        if guard is not None:
            with guard:
                result = self.backend.execute(action)
        else:
            result = self.backend.execute(action)
        if guard is not None:
            result.details["focus_guard"] = guard.summary()
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
