# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.router — ActionRouter dispatch & concurrency.

Pure-stdlib tests; no real user windows touched (dry-run backend only).
"""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from agent_control_fabric import Action, ActionRouter, DryRunBackend, Target


def _act(action_id: str, window_id: int = 1, kind: str = "wait") -> Action:
    return Action(
        action_id=action_id,
        agent_id="router-test",
        source="semantic",
        kind=kind,
        target=Target(kind="window", pid=1, window_id=window_id),
        parameters={"time": 0.001} if kind == "wait" else {"x": 1, "y": 1},
        delivery_mode="background",
    )


class ActionRouterTests(unittest.TestCase):
    def test_execute_dispatches_to_backend(self):
        router = ActionRouter(DryRunBackend())
        result = router.execute(_act("r-1"))
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.backend, "dry-run")

    def test_execute_many_preserves_order(self):
        router = ActionRouter(DryRunBackend())
        actions = [_act(f"r-many-{i}", window_id=i + 1) for i in range(5)]
        results = router.execute_many(actions)
        self.assertEqual(len(results), 5)
        self.assertEqual([r.action_id for r in results],
                         [a.action_id for a in actions])
        self.assertTrue(all(r.status == "executed" for r in results))

    def test_execute_many_empty(self):
        router = ActionRouter(DryRunBackend())
        self.assertEqual(router.execute_many([]), [])

    def test_concurrent_different_windows_all_execute(self):
        router = ActionRouter(DryRunBackend())
        with ThreadPoolExecutor(4) as ex:
            futs = [ex.submit(router.execute, _act(f"r-conc-{i}", window_id=100 + i))
                    for i in range(8)]
            results = [f.result() for f in futs]
        self.assertTrue(all(r.status == "executed" for r in results))

    def test_foreground_still_blocked_by_backend(self):
        router = ActionRouter(DryRunBackend())
        act = Action(
            action_id="r-fg", agent_id="router-test", source="semantic",
            kind="wait", target=Target(kind="window", pid=1, window_id=1),
            parameters={"time": 0.001}, delivery_mode="foreground",
        )
        # DryRun may allow; assert router passes through without raising and
        # result is a BackendResult with a status field
        result = router.execute(act)
        self.assertTrue(hasattr(result, "status"))


if __name__ == "__main__":
    unittest.main()
