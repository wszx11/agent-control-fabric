# -*- coding: utf-8 -*-
"""Tests for strict no-foreground / focus-guard enforcement across ACF."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_control_fabric import (
    NO_FOREGROUND_ENV,
    Action,
    ActionRouter,
    DryRunBackend,
    InputSnapshot,
    Target,
    UIABackend,
    Win32Backend,
    check_foreground_allowed,
    is_foreground_request,
)
from agent_control_fabric.backends import CuaDriverBackend
from agent_control_fabric.cli import main as acf_cli_main

try:
    from ._isolation import IsolatedLockDirMixin
except ImportError:
    from _isolation import IsolatedLockDirMixin


def _win_action(
    kind: str = "click",
    *,
    delivery_mode: str = "background",
    params: dict | None = None,
) -> Action:
    return Action(
        action_id="fg-test-1",
        agent_id="unit",
        source="semantic",
        kind=kind,
        target=Target(kind="window", pid=100, window_id=200),
        parameters=params if params is not None else {"x": 5, "y": 5},
        delivery_mode=delivery_mode,
    )


class NoForegroundPolicyTests(IsolatedLockDirMixin, unittest.TestCase):
    def test_is_foreground_request_detects_all_vectors(self):
        from types import SimpleNamespace
        self.assertTrue(is_foreground_request(_win_action(delivery_mode="foreground"))[0])
        self.assertTrue(is_foreground_request(_win_action(params={"x": 1, "y": 1, "bring_to_front": True}))[0])
        self.assertTrue(is_foreground_request(_win_action(params={"x": 1, "y": 1, "raise_window": True}))[0])
        focus_like = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters={})
        self.assertTrue(is_foreground_request(focus_like)[0])
        focus_safe = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters={"raise_window": False})
        self.assertFalse(is_foreground_request(focus_safe)[0])
        self.assertFalse(is_foreground_request(_win_action(delivery_mode="background"))[0])

    def test_is_foreground_request_handles_none_and_non_dict_parameters(self):
        from types import SimpleNamespace
        for bad_params in (None, [], ["bring_to_front"], "raise_window", 123, object()):
            act = SimpleNamespace(kind="click", delivery_mode="background", parameters=bad_params)
            is_fg, reason = is_foreground_request(act)
            self.assertFalse(is_fg, f"unexpectedly flagged {bad_params!r}: {reason}")
            self.assertEqual(reason, "")

        focus_none = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters=None)
        self.assertTrue(is_foreground_request(focus_none)[0])
        focus_list = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters=["bad"])
        self.assertTrue(is_foreground_request(focus_list)[0])

    def test_is_foreground_request_strict_bool_parsing(self):
        from types import SimpleNamespace
        for key in ("bring_to_front", "raise_window"):
            for falsy_val in (False, 0, "0", "false", "FALSE", " false ", "no", "off", "", "maybe", 2):
                act = _win_action(params={"x": 1, "y": 1, key: falsy_val})
                self.assertFalse(
                    is_foreground_request(act)[0],
                    f"{key}={falsy_val!r} should NOT be treated as foreground request",
                )
            for truthy_val in (True, 1, "1", "true", " TRUE ", "yes", "Yes", "on", "ON"):
                act = _win_action(params={"x": 1, "y": 1, key: truthy_val})
                self.assertTrue(
                    is_foreground_request(act)[0],
                    f"{key}={truthy_val!r} SHOULD be treated as foreground request",
                )

        focus_str_false = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters={"raise_window": "false"})
        self.assertFalse(is_foreground_request(focus_str_false)[0])
        focus_str_zero = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters={"raise_window": "0"})
        self.assertFalse(is_foreground_request(focus_str_zero)[0])
        focus_str_true = SimpleNamespace(kind="focus_app", delivery_mode="background", parameters={"raise_window": "true"})
        self.assertTrue(is_foreground_request(focus_str_true)[0])

    def test_router_blocks_all_foreground_vectors_by_default(self):
        backend = DryRunBackend()
        router = ActionRouter(backend)
        for act in (
            _win_action(delivery_mode="foreground"),
            _win_action(params={"x": 1, "y": 1, "bring_to_front": True}),
            _win_action(params={"x": 1, "y": 1, "raise_window": True}),
            _win_action(kind="type", delivery_mode="foreground", params={"text": "hi"}),
        ):
            res = router.execute(act)
            self.assertEqual(res.status, "blocked")
            self.assertEqual(res.backend, "router-no-foreground")
            self.assertEqual(res.details["code"], "foreground_not_allowed")

    def test_env_acf_no_foreground_overrides_explicit_allow(self):
        act = _win_action(delivery_mode="foreground")
        with patch.dict(os.environ, {NO_FOREGROUND_ENV: "1"}, clear=False):
            allowed, reason = check_foreground_allowed(act, allow_foreground=True, no_foreground=False)
            self.assertFalse(allowed)
            self.assertIn(NO_FOREGROUND_ENV, reason)

            router = ActionRouter(DryRunBackend(), no_foreground=False)
            res = router.execute(act)
            self.assertEqual(res.status, "blocked")
            self.assertEqual(res.details["code"], "foreground_not_allowed")

            for backend in (
                Win32Backend(allow_foreground=True),
                CuaDriverBackend(allow_foreground=True),
                UIABackend(allow_foreground=True),
            ):
                b_res = backend.execute(act)
                self.assertEqual(b_res.status, "blocked")
                self.assertEqual(b_res.details["code"], "foreground_not_allowed")

    def test_backends_block_bring_to_front_and_raise_window_parameters(self):
        for act in (
            _win_action(params={"x": 1, "y": 1, "bring_to_front": True}),
            _win_action(params={"x": 1, "y": 1, "raise_window": True}),
        ):
            for backend in (Win32Backend(), CuaDriverBackend(), UIABackend()):
                res = backend.execute(act)
                self.assertEqual(res.status, "blocked")
                self.assertEqual(res.details["code"], "foreground_not_allowed")

    def test_router_focus_guard_records_summary_and_restores_drift_without_setforeground(self):
        class _DriftingBackend:
            name = "fake-drift"

            def execute(self, action: Action):
                from agent_control_fabric.models import BackendResult
                return BackendResult(status="executed", action_id=action.action_id, backend=self.name, details={})

        snaps = [
            InputSnapshot(foreground_hwnd=111, cursor_x=10, cursor_y=20, user_idle_ms=5),
            InputSnapshot(foreground_hwnd=222, cursor_x=10, cursor_y=20, user_idle_ms=5),
            InputSnapshot(foreground_hwnd=111, cursor_x=10, cursor_y=20, user_idle_ms=5),
        ]
        fake_user32 = MagicMock()
        fake_user32.IsWindow.return_value = True
        fake_user32.GetWindowRect.return_value = True

        with (
            patch("agent_control_fabric.layout.IS_WINDOWS", True),
            patch("agent_control_fabric.layout.user32", fake_user32),
            patch("agent_control_fabric.layout.sample_input_state", side_effect=snaps),
            patch("agent_control_fabric.layout.time.sleep"),
        ):
            router = ActionRouter(_DriftingBackend())
            res = router.execute(_win_action())

        self.assertEqual(res.status, "executed")
        fg = res.details["focus_guard"]
        self.assertTrue(fg["checked"])
        self.assertTrue(fg["drifted"])
        self.assertTrue(fg["drifted_restored"])
        self.assertEqual(fg["drifted_hwnd"], 222)
        self.assertTrue(fg["focus_preserved"])
        self.assertFalse(fake_user32.SetForegroundWindow.called)
        fake_user32.ShowWindow.assert_any_call(222, 6)
        fake_user32.ShowWindow.assert_any_call(222, 4)
        self.assertTrue(fake_user32.SetWindowPos.called)

    def test_env_acf_no_foreground_zero_preserves_instance_no_foreground_true(self):
        act = _win_action(delivery_mode="foreground")
        with patch.dict(os.environ, {NO_FOREGROUND_ENV: "0"}, clear=False):
            router_strict = ActionRouter(DryRunBackend(), no_foreground=True)
            res = router_strict.execute(act)
            self.assertEqual(res.status, "blocked")
            self.assertEqual(res.backend, "router-no-foreground")
            self.assertEqual(res.details["code"], "foreground_not_allowed")

            router_open = ActionRouter(DryRunBackend(), no_foreground=False)
            res_open = router_open.execute(act)
            self.assertEqual(res_open.status, "executed")

    def test_backends_block_details_consistent_across_all_three_backends(self):
        expected_keys = {"code", "message"}
        for act in (
            _win_action(delivery_mode="foreground"),
            _win_action(params={"x": 1, "y": 1, "bring_to_front": True}),
            _win_action(params={"x": 1, "y": 1, "raise_window": "true"}),
        ):
            messages = []
            for backend in (Win32Backend(), CuaDriverBackend(), UIABackend()):
                res = backend.execute(act)
                self.assertEqual(res.status, "blocked")
                self.assertEqual(set(res.details.keys()), expected_keys)
                self.assertEqual(res.details["code"], "foreground_not_allowed")
                self.assertTrue(isinstance(res.details["message"], str) and res.details["message"])
                messages.append(res.details["message"])
            self.assertEqual(len(set(messages)), 1, f"inconsistent backend block messages: {messages}")

    def test_user_input_guard_off_windows_and_none_user32_summary_complete(self):
        from agent_control_fabric import UserInputGuard
        expected_keys = {
            "checked",
            "focus_preserved",
            "drifted",
            "drifted_restored",
            "drifted_hwnd",
            "cursor_unchanged",
            "before",
            "after",
        }
        for is_win, u32 in ((False, None), (True, None)):
            with (
                patch("agent_control_fabric.layout.IS_WINDOWS", is_win),
                patch("agent_control_fabric.layout.user32", u32),
            ):
                guard = UserInputGuard(restore_foreground_on_drift=True)
                pre_summary = guard.summary()
                self.assertEqual(set(pre_summary.keys()), expected_keys)
                self.assertFalse(pre_summary["checked"])
                with guard:
                    pass
                post_summary = guard.summary()
                self.assertEqual(set(post_summary.keys()), expected_keys)
                self.assertTrue(post_summary["checked"])
                self.assertTrue(post_summary["focus_preserved"])
                self.assertFalse(post_summary["drifted"])
                self.assertFalse(post_summary["drifted_restored"])
                self.assertEqual(post_summary["drifted_hwnd"], 0)
                self.assertTrue(post_summary["cursor_unchanged"])
                self.assertIsInstance(post_summary["before"], dict)
                self.assertIsInstance(post_summary["after"], dict)

    def test_cli_no_foreground_overrides_allow_foreground(self):
        payload = [
            {
                "action_id": "cli-fg-1",
                "agent_id": "unit",
                "source": "semantic",
                "kind": "click",
                "target": {"kind": "window", "pid": 1, "window_id": 2},
                "parameters": {"x": 5, "y": 5},
                "delivery_mode": "foreground",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "actions.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            with patch("builtins.print"):
                rc = acf_cli_main([
                    "run", str(p),
                    "--backend", "win32",
                    "--allow-foreground",
                    "--no-foreground",
                ])
        self.assertEqual(rc, 2)

    def test_cli_default_blocks_foreground_and_allow_foreground_permits_dry_run(self):
        payload = [
            {
                "action_id": "cli-fg-2",
                "agent_id": "unit",
                "source": "semantic",
                "kind": "click",
                "target": {"kind": "window", "pid": 1, "window_id": 2},
                "parameters": {"x": 5, "y": 5},
                "delivery_mode": "foreground",
            }
        ]
        env_clean = dict(os.environ)
        env_clean.pop(NO_FOREGROUND_ENV, None)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "actions.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, env_clean, clear=True):
                with patch("builtins.print"):
                    rc_default = acf_cli_main(["run", str(p), "--backend", "dry-run"])
                    rc_allowed = acf_cli_main([
                        "run", str(p),
                        "--backend", "dry-run",
                        "--allow-foreground",
                    ])
        self.assertEqual(rc_default, 2)
        self.assertEqual(rc_allowed, 0)


if __name__ == "__main__":
    unittest.main()
