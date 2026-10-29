# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.cloudpc — A2A mailbox & status probing."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import unittest

from agent_control_fabric import cloudpc_status, enqueue_cloudpc_a2a


class CloudpcA2ATests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acf_cloudpc_"))

    def test_enqueue_writes_inbox_jsonl(self):
        out = enqueue_cloudpc_a2a("probe-001", "hostname", a2a_dir=self.tmp, timeout_s=30)
        self.assertTrue(out["ok"])
        inbox = self.tmp / "inbox.jsonl"
        self.assertTrue(inbox.exists())
        lines = inbox.read_text(encoding="utf-8").strip().splitlines()
        rec = json.loads(lines[-1])
        self.assertEqual(rec["task_id"], "probe-001")
        self.assertEqual(rec["cmd"], "hostname")

    def test_enqueue_custom_mode_and_note(self):
        out = enqueue_cloudpc_a2a("probe-002", "whoami", a2a_dir=self.tmp,
                                  timeout_s=5, note="unit")
        self.assertTrue(out["ok"])
        lines = (self.tmp / "inbox.jsonl").read_text(encoding="utf-8").strip().splitlines()
        rec = json.loads(lines[-1])
        self.assertEqual(rec.get("mode"), "shell")
        self.assertEqual(rec.get("note"), "unit")

    def test_status_reports_pending_and_outbox(self):
        enqueue_cloudpc_a2a("probe-003", "echo hi", a2a_dir=self.tmp, timeout_s=5)
        st = cloudpc_status(a2a_dir=self.tmp)
        self.assertIsInstance(st, dict)
        self.assertEqual(st["a2a"]["inbox_count"], 1)
        self.assertTrue(st["a2a"]["inbox_exists"])

    def test_status_empty_dir_does_not_crash(self):
        st = cloudpc_status(a2a_dir=self.tmp / "nope")
        self.assertIsInstance(st, dict)

    def test_resolve_cua_driver_path(self):
        from agent_control_fabric.cloudpc import resolve_cua_driver_path
        # Nonexistent custom path should fallback
        res = resolve_cua_driver_path("C:/fake/path/to/cua-driver.exe")
        self.assertIsInstance(res, str)
        self.assertTrue(len(res) > 0)

    def test_peek_cloudpc_screen_unsupported_mode(self):
        from agent_control_fabric.cloudpc import peek_cloudpc_screen
        res = peek_cloudpc_screen(self.tmp / "test.png", mode="invalid_mode")
        self.assertFalse(res["ok"])
        self.assertIn("unsupported", res.get("error", ""))

    def test_route_cloudpc_window_action_dry_run(self):
        from agent_control_fabric.cloudpc import route_cloudpc_window_action
        target_win = {"pid": 1234, "window_id": 5678, "title": "MockWindow"}
        res = route_cloudpc_window_action(
            kind="click",
            parameters={"point": [100, 100]},
            backend_mode="dry-run",
            target_window=target_win,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["backend"], "dry-run")
        self.assertEqual(res["target"]["window_id"], 5678)


if __name__ == "__main__":
    unittest.main()
