# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.layout and .verification (read-only paths)."""
from __future__ import annotations

import unittest

from agent_control_fabric import (
    Action,
    InputSnapshot,
    StaticVerifier,
    Target,
    UserInputGuard,
    sample_input_state,
)


class LayoutTests(unittest.TestCase):
    def test_sample_input_state_shape(self):
        snap = sample_input_state()
        self.assertIsInstance(snap, InputSnapshot)
        self.assertIsInstance(snap.cursor_x, int)
        self.assertIsInstance(snap.cursor_y, int)

    def test_guard_summary_reports_checked(self):
        g = UserInputGuard(restore_foreground_on_drift=False)
        with g:
            pass
        summary = g.summary()
        self.assertIn("checked", summary)


class VerificationTests(unittest.TestCase):
    def _act(self, expected):
        return Action(
            action_id="v-1", agent_id="t", source="semantic", kind="click",
            target=Target(kind="window", pid=1, window_id=1),
            parameters={"x": 5, "y": 5}, delivery_mode="background",
            expected=expected,
        )

    def test_static_verifier_returns_configured_status(self):
        verifier = StaticVerifier("verified")
        res = verifier.verify(self._act(({"field": "window_exists", "equals": True},)))
        self.assertEqual(res.status, "verified")

    def test_static_verifier_accepts_failed_status(self):
        verifier = StaticVerifier("failed", {"reason": "unit"})
        res = verifier.verify(self._act(()))
        self.assertEqual(res.status, "failed")

    def test_observation_verifier_uses_observable(self):
        from agent_control_fabric.verification import ObservationVerifier

        def observe(target):
            return {"window_exists": True, "elements": []}

        ov = ObservationVerifier(observe)
        act = self._act(({"field": "window_exists", "equals": True},))
        res = ov.verify(act)
        self.assertIsNotNone(res.status)


if __name__ == "__main__":
    unittest.main()
