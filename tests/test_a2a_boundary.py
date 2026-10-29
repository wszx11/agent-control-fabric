# -*- coding: utf-8 -*-
"""Tests for the deny-by-default A2A operation boundary."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_control_fabric.a2a_boundary import (  # noqa: E402
    ALLOWED_OPS, FORBIDDEN_OPS, REFUSAL_CODE, describe, is_allowed, normalize_op, refusal,
)


class A2ABoundaryTests(unittest.TestCase):
    def test_only_the_readonly_probe_is_allowed(self):
        self.assertEqual(set(ALLOWED_OPS), {"branch_status_probe"})
        self.assertTrue(is_allowed("branch_status_probe"))

    def test_observed_script_attempt_is_refused(self):
        # This exact op was seen in the mailbox on 2026-10-29 and must stay refused.
        self.assertFalse(is_allowed("run_script"))
        self.assertEqual(refusal("run_script"), {"ok": False, "error": REFUSAL_CODE, "op": "run_script"})

    def test_every_documented_forbidden_op_is_refused(self):
        still_open = [op for op in FORBIDDEN_OPS if is_allowed(op)]
        self.assertEqual(still_open, [], f"forbidden ops wrongly allowed: {still_open}")

    def test_deny_by_default_for_arbitrary_names(self):
        for op in ("", "   ", None, 123, "make_me_a_sandwich", "branch_status_probe_extra"):
            self.assertFalse(is_allowed(op), repr(op))

    def test_normalization_is_case_and_whitespace_insensitive(self):
        self.assertEqual(normalize_op("  Branch_Status_Probe "), "branch_status_probe")
        self.assertTrue(is_allowed(" Branch_Status_Probe "))
        self.assertTrue(is_allowed(b"branch_status_probe"))

    def test_allowed_and_forbidden_do_not_overlap(self):
        self.assertEqual(ALLOWED_OPS & FORBIDDEN_OPS, frozenset())

    def test_describe_is_machine_readable(self):
        info = describe()
        self.assertEqual(info["policy"], "deny-by-default")
        self.assertEqual(info["allowed_ops"], ["branch_status_probe"])
        self.assertIn("run_script", info["forbidden_ops"])
        self.assertEqual(info["refusal_code"], REFUSAL_CODE)


if __name__ == "__main__":
    unittest.main()
