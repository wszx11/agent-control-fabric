# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.retrospect — trajectory recording & analysis."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_control_fabric import Action, BackendResult, Target


class RetrospectRecordTests(unittest.TestCase):
    def test_record_trajectory_appends_atomic(self):
        from agent_control_fabric.retrospect import record_trajectory, trajectory_path
        act = Action(
            action_id="retro-test-1", agent_id="t", source="semantic", kind="wait",
            target=Target(kind="window", pid=1, window_id=1),
            parameters={"time": 0.001}, delivery_mode="background",
        )
        res = BackendResult(status="executed", action_id="retro-test-1",
                            backend="dry-run", details={})
        before = len(Path(trajectory_path()).read_text(encoding="utf-8").strip().splitlines()) \
            if Path(trajectory_path()).exists() else 0
        record_trajectory(act, res, elapsed_ms=1.5)
        after = len(Path(trajectory_path()).read_text(encoding="utf-8").strip().splitlines())
        self.assertEqual(after, before + 1)


class RetrospectRunTests(unittest.TestCase):
    def test_run_retrospect_returns_report(self):
        from agent_control_fabric.retrospect import run_retrospect
        rep = run_retrospect(limit=50)
        self.assertIsNotNone(rep)
        self.assertGreaterEqual(rep.total_actions, 0)
        self.assertIsInstance(rep.findings, list)
        self.assertIsInstance(rep.next_iterations, list)
        self.assertTrue(0.0 <= rep.success_rate <= 1.0)


if __name__ == "__main__":
    unittest.main()
