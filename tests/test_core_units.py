# -*- coding: utf-8 -*-
"""Unit tests for the three logic-only modules (no OS contact at all):
models (validation contract), normalize (dialect routing), backends (dispatch).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_control_fabric import (  # noqa: E402
    Action, ActionValidationError, BackendResult, CompositeBackend, DryRunBackend,
    FunctionBackend, Target, normalize_action,
)

WIN = {"kind": "window", "pid": 42, "window_id": 7}


def _action(**kw):
    base = dict(action_id="a1", agent_id="ag", source="generic", kind="click",
                target=Target(**WIN))
    base.update(kw)
    return Action(**base)


class TargetTests(unittest.TestCase):
    def test_rejects_unknown_kind(self):
        with self.assertRaises(ActionValidationError):
            Target(kind="screen", pid=1, window_id=1)

    def test_kind_is_normalized(self):
        self.assertEqual(Target(kind=" WINDOW ", pid=1, window_id=2).kind, "window")

    def test_window_requires_positive_ids(self):
        for kw in ({"pid": 0, "window_id": 1}, {"pid": 1, "window_id": 0},
                   {"pid": None, "window_id": 1}, {"pid": 1, "window_id": None}):
            with self.assertRaises(ActionValidationError):
                Target(kind="window", **kw)

    def test_browser_requires_session_or_tab(self):
        with self.assertRaises(ActionValidationError):
            Target(kind="browser")
        self.assertEqual(Target(kind="browser", tab_id="t1").tab_id, "t1")

    def test_from_mapping_accepts_session_alias_and_passthrough(self):
        t = Target.from_mapping({"kind": "browser", "session": "s9"})
        self.assertEqual(t.session_id, "s9")
        self.assertIs(Target.from_mapping(t), t)
        with self.assertRaises(ActionValidationError):
            Target.from_mapping("not-a-mapping")

    def test_to_dict_omits_none_fields(self):
        self.assertEqual(Target(**WIN).to_dict(), WIN)
        self.assertEqual(Target(kind="browser", tab_id="t").to_dict(),
                         {"kind": "browser", "tab_id": "t"})

    def test_lock_key_ignores_session_for_windows_keeps_tab_for_browser(self):
        a = Target(kind="window", pid=1, window_id=2, session_id="s1")
        b = Target(kind="window", pid=1, window_id=2, session_id="s2")
        self.assertEqual(a.lock_key(), b.lock_key())
        self.assertEqual(a.lock_key(), ("window", 1, 2))
        self.assertEqual(Target(kind="browser", tab_id="t1").lock_key(),
                         ("browser", None, "t1"))


class ActionTests(unittest.TestCase):
    def test_requires_ids(self):
        for kw in ({"action_id": "  "}, {"agent_id": " "}):
            with self.assertRaises(ActionValidationError):
                _action(**kw)

    def test_rejects_bad_kind_and_delivery(self):
        with self.assertRaises(ActionValidationError):
            _action(kind="teleport")
        with self.assertRaises(ActionValidationError):
            _action(delivery_mode="silent")

    def test_requires_target_instance(self):
        with self.assertRaises(ActionValidationError):
            _action(target=WIN)

    def test_requires_mapping_parameters(self):
        with self.assertRaises(ActionValidationError):
            _action(parameters=["x"])

    def test_normalizes_case_and_defaults(self):
        a = _action(kind=" CLICK ", source=" SEMANTIC ", delivery_mode=" BACKGROUND ",
                    expected=[{"visible": True}], parameters={"x": 1})
        self.assertEqual((a.kind, a.source, a.delivery_mode), ("click", "semantic", "background"))
        self.assertIsInstance(a.expected, tuple)
        self.assertEqual(a.expected, ({"visible": True},))

    def test_roundtrip_mapping(self):
        a = _action(parameters={"x": 1}, expected=[{"ok": True}])
        b = Action.from_mapping(a.to_dict())
        self.assertEqual(b.to_dict(), a.to_dict())
        with self.assertRaises(ActionValidationError):
            Action.from_mapping("nope")


class BackendResultTests(unittest.TestCase):
    def test_ok_property(self):
        for status, ok in (("executed", True), ("verified", True), ("blocked", False),
                           ("failed", False), ("unknown", False)):
            self.assertIs(BackendResult(status, "a", "b").ok, ok, status)

    def test_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            BackendResult("finished", "a", "b")


class NormalizeTests(unittest.TestCase):
    def test_each_dialect_routes_to_canonical_kind(self):
        cases = [
            ("coordinate", {"action": "left_click", "start_box": [500, 500]}),
            ("dsl", {"action": "click", "point": [10, 20]}),
            ("semantic", {"action": "click", "selector": {"name": "OK", "role": "Button"}}),
            ("generic", {"kind": "type", "text": "hi"}),
        ]
        for source, raw in cases:
            a = normalize_action(source, raw, target=WIN, action_id=f"n-{source}")
            self.assertEqual(a.action_id, f"n-{source}")
            self.assertIn(a.kind, {"click", "type"}, source)

    def test_alias_table_maps_verbs(self):
        a = normalize_action("dsl", "input(text='x')", target=WIN, action_id="al")
        self.assertEqual(a.kind, "type")
        b = normalize_action("coordinate", {"action": "left_double_click",
                                            "start_box": [10, 10]}, target=WIN, action_id="al2")
        self.assertEqual(b.kind, "double_click")

    def test_normalized_1000_scaling(self):
        a = normalize_action("coordinate", {"action": "click", "start_box": [500, 250]},
                             target=WIN, action_id="sc", coord_space="normalized_1000",
                             window_size=(1000, 500))
        p = a.parameters
        coords = [v for v in p.values() if isinstance(v, (int, float))]
        self.assertIn(500, coords)
        self.assertIn(125, coords)

    def test_unknown_dialect_raises(self):
        with self.assertRaises(Exception):
            normalize_action("made-up", {"action": "click"}, target=WIN)

    def test_embedded_metadata_is_consumed_not_leaked(self):
        a = normalize_action("dsl", {"action": "click", "point": [5, 5],
                                     "action_id": "in-id",
                                     "agent_id": "in-agent",
                                     "delivery_mode": "foreground"},
                             target=WIN)
        self.assertEqual((a.action_id, a.agent_id, a.delivery_mode),
                         ("in-id", "in-agent", "foreground"))
        for leaked in ("action_id", "agent_id", "delivery_mode"):
            self.assertNotIn(leaked, a.parameters)


class BackendTests(unittest.TestCase):
    def test_dry_run_echoes_action(self):
        res = DryRunBackend().execute(_action())
        self.assertEqual(res.status, "executed")
        self.assertTrue(res.details["dry_run"])
        self.assertEqual(res.details["action"]["action_id"], "a1")

    def test_function_backend_wraps_and_catches(self):
        res = FunctionBackend("fn", lambda a: {"echo": a.action_id}).execute(_action())
        self.assertEqual(res.details["result"], {"echo": "a1"})
        boom = FunctionBackend("fn", lambda a: 1 / 0).execute(_action())
        self.assertEqual(boom.status, "failed")
        self.assertEqual(boom.details["error_type"], "ZeroDivisionError")

    def test_function_backend_passes_through_result_object(self):
        sentinel = BackendResult("verified", "a1", "fn")
        self.assertIs(FunctionBackend("fn", lambda a: sentinel).execute(_action()), sentinel)

    def test_composite_requires_backends(self):
        with self.assertRaises(ValueError):
            CompositeBackend([])

    def test_composite_falls_back_and_records_origin(self):
        class Failing:
            name = "boom"

            def execute(self, action):
                return BackendResult("failed", action.action_id, self.name)

        res = CompositeBackend([Failing(), DryRunBackend()]).execute(_action())
        self.assertEqual(res.status, "executed")
        self.assertEqual(res.details["fallback_from"], "boom")

    def test_composite_returns_first_result_when_all_fail(self):
        class Failing:
            name = "boom"

            def execute(self, action):
                return BackendResult("failed", action.action_id, self.name, {"code": "x"})

        res = CompositeBackend([Failing(), Failing()]).execute(_action())
        self.assertEqual((res.status, res.backend), ("failed", "boom"))


if __name__ == "__main__":
    unittest.main()
