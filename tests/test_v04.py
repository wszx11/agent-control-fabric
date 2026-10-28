"""Unit tests for agent-control-fabric v0.4.0 capabilities."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_control_fabric import (
    Action,
    Target,
    UIABackend,
    CoordinateAdapter,
    SemanticAdapter,
    UserInputGuard,
    enqueue_cloudpc_a2a,
    sample_input_state,
)
from agent_control_fabric.capture import _encode_rgb_png


class TestV04Capabilities(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acf_test_v04_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_coordinate_dialect_1000_scaling(self):
        adapter = CoordinateAdapter()
        act = adapter.normalize(
            "click(start_box='[500, 250, 500, 250]')",
            target=Target(kind="window", pid=100, window_id=200),
            action_id="scale-1",
            agent_id="visual-agent",
            coord_space="normalized_1000",
            window_size=(1200, 800),
        )
        self.assertEqual(act.parameters["x"], 600.0)
        self.assertEqual(act.parameters["y"], 200.0)

    def test_semantic_selector_normalization(self):
        adapter = SemanticAdapter()
        act = adapter.normalize(
            {
                "command": "click",
                "params": {"selector": {"name": "文件传输", "role": "RadioButton"}},
            },
            target=Target(kind="window", pid=100, window_id=200),
            action_id="sem-1",
            agent_id="semantic-agent",
        )
        self.assertEqual(act.kind, "click")
        self.assertEqual(act.parameters["selector"]["name"], "文件传输")

    def test_uia_backend_blocks_foreground_and_missing_selector(self):
        b = UIABackend()
        fg_act = Action(
            action_id="u-fg",
            agent_id="a1",
            source="ufo2",
            kind="click",
            target=Target(kind="window", pid=1, window_id=1),
            parameters={"selector": {"name": "OK"}},
            delivery_mode="foreground",
        )
        res_fg = b.execute(fg_act)
        self.assertEqual(res_fg.status, "blocked")

        no_sel = Action(
            action_id="u-nosel",
            agent_id="a1",
            source="ufo2",
            kind="click",
            target=Target(kind="window", pid=1, window_id=1),
            parameters={"x": 10, "y": 10},
        )
        res_nosel = b.execute(no_sel)
        self.assertEqual(res_nosel.status, "failed")
        self.assertEqual(res_nosel.details.get("code"), "selector_required")

    def test_stdlib_png_encoder_produces_valid_png_signature(self):
        # 2x2 BGRA image (blue, green, red, white)
        bgra = bytes([
            255, 0, 0, 255,    0, 255, 0, 255,
            0, 0, 255, 255,    255, 255, 255, 255,
        ])
        png = _encode_rgb_png(2, 2, bgra)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png)
        self.assertIn(b"IDAT", png)
        self.assertTrue(png.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"))

    def test_user_input_guard_snapshot(self):
        snap = sample_input_state()
        self.assertIsInstance(snap.cursor_x, int)
        with UserInputGuard(restore_foreground_on_drift=False) as guard:
            pass
        summary = guard.summary()
        self.assertTrue(summary["checked"])

    def test_cloudpc_a2a_enqueue(self):
        a2a_dir = Path(self.tmp) / "a2a"
        out = enqueue_cloudpc_a2a("task-001", "hostname", a2a_dir=a2a_dir, timeout_s=30, note="unit-test")
        self.assertTrue(out["ok"])
        inbox = a2a_dir / "inbox.jsonl"
        self.assertTrue(inbox.exists())
        self.assertIn("task-001", inbox.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
