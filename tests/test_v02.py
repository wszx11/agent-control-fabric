"""Unit tests for agent-control-fabric v0.2.0 features."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from agent_control_fabric.backends import CompositeBackend, DryRunBackend, FunctionBackend
from agent_control_fabric.locks import LockError, ResourceLock, active_keys, peek
from agent_control_fabric.models import Action, BackendResult, Target
from agent_control_fabric.retrospect import record_trajectory, run_retrospect
from agent_control_fabric.router import ActionRouter
from agent_control_fabric.todesk import prepare_sanitized_bundle
from agent_control_fabric.win32_backend import Win32Backend


class TestLocksV02(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acf_test_locks_")
        self.old_env = os.environ.get("ACF_LOCK_DIR")
        os.environ["ACF_LOCK_DIR"] = self.tmp

    def tearDown(self):
        if self.old_env is not None:
            os.environ["ACF_LOCK_DIR"] = self.old_env
        else:
            os.environ.pop("ACF_LOCK_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acquire_and_release(self):
        lock = ResourceLock("test:res1", agent="agentA", ttl=10.0)
        self.assertTrue(lock.acquire(timeout=1.0))
        self.assertIn("test:res1", active_keys())
        p = peek("test:res1")
        self.assertIsNotNone(p)
        self.assertEqual(p["agent"], "agentA")
        self.assertFalse(p["_expired"])
        self.assertTrue(lock.release())
        self.assertNotIn("test:res1", active_keys())

    def test_lock_contention(self):
        l1 = ResourceLock("test:res2", agent="agent1", ttl=5.0)
        l1.acquire()
        l2 = ResourceLock("test:res2", agent="agent2", ttl=5.0, poll=0.01)
        with self.assertRaises(LockError):
            l2.acquire(timeout=0.05)
        l1.release()
        self.assertTrue(l2.acquire(timeout=0.5))
        l2.release()

    def test_expired_takeover(self):
        l1 = ResourceLock("test:res3", agent="agentOld", ttl=0.05, poll=0.01)
        l1.acquire()
        time.sleep(0.1)
        l2 = ResourceLock("test:res3", agent="agentNew", ttl=5.0, poll=0.01)
        self.assertTrue(l2.acquire(timeout=0.5))
        p = peek("test:res3")
        self.assertEqual(p["agent"], "agentNew")
        l2.release()


class TestWin32BackendValidation(unittest.TestCase):
    def test_target_non_window_is_blocked(self):
        backend = Win32Backend()
        action = Action(
            action_id="a1",
            agent_id="test",
            source="manual",
            kind="click",
            target=Target(kind="browser", tab_id="tab1"),
            parameters={"x": 10, "y": 20},
        )
        res = backend.execute(action)
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.details.get("code"), "target_not_supported")

    def test_foreground_blocked_by_default(self):
        backend = Win32Backend(allow_foreground=False)
        action = Action(
            action_id="a2",
            agent_id="test",
            source="manual",
            kind="click",
            target=Target(kind="window", pid=1, window_id=99999999),
            delivery_mode="foreground",
            parameters={"x": 10, "y": 20},
        )
        res = backend.execute(action)
        self.assertEqual(res.status, "blocked")
        self.assertEqual(res.details.get("code"), "foreground_not_allowed")

    def test_wait_action_executed(self):
        backend = Win32Backend()
        action = Action(
            action_id="a3",
            agent_id="test",
            source="manual",
            kind="wait",
            target=Target(kind="window", pid=1, window_id=1),
            parameters={"time": 0.01},
        )
        res = backend.execute(action)
        self.assertEqual(res.status, "executed")
        self.assertEqual(res.details.get("tool"), "wait")


class TestCompositeBackend(unittest.TestCase):
    def test_primary_succeeds(self):
        b1 = DryRunBackend()
        b2 = FunctionBackend("dummy", lambda a: BackendResult(status="failed", action_id=a.action_id, backend="dummy"))
        comp = CompositeBackend([b1, b2])
        action = Action(
            action_id="c1",
            agent_id="test",
            source="manual",
            kind="click",
            target=Target(kind="window", pid=1, window_id=2),
        )
        res = comp.execute(action)
        self.assertEqual(res.status, "executed")
        self.assertEqual(res.backend, "dry-run")

    def test_primary_fails_fallback_succeeds(self):
        b1 = FunctionBackend("failing", lambda a: BackendResult(status="failed", action_id=a.action_id, backend="failing"))
        b2 = DryRunBackend()
        comp = CompositeBackend([b1, b2])
        action = Action(
            action_id="c2",
            agent_id="test",
            source="manual",
            kind="click",
            target=Target(kind="window", pid=1, window_id=2),
        )
        res = comp.execute(action)
        self.assertEqual(res.status, "executed")
        self.assertEqual(res.details.get("fallback_from"), "failing")


class TestToDeskSanitizedStaging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acf_test_todesk_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_password_files_strictly_excluded(self):
        src = Path(self.tmp) / "src"
        src.mkdir()
        (src / "antigravity_rt_export.tar.enc").write_bytes(b"ENC_PAYLOAD_1234567890")
        (src / "antigravity_rt_export.sha256.txt").write_text("dummy sha antigravity_rt_export.tar.enc", encoding="utf-8")
        # Attacker/accidental sensitive file:
        (src / "antigravity_rt_export.password.txt").write_text("SUPER_SECRET_PLAINTEXT", encoding="utf-8")

        out_zip = Path(self.tmp) / "sanitized.zip"
        res = prepare_sanitized_bundle(src, out_zip)

        self.assertEqual(res["included_count"], 2)
        self.assertEqual(res["rejected_passwords_count"], 1)
        self.assertIn("antigravity_rt_export.password.txt", res["manifest"]["security_rejected_plaintext_passwords"])

        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
            self.assertIn("antigravity_rt_export.tar.enc", names)
            self.assertIn("antigravity_rt_export.sha256.txt", names)
            self.assertIn("MANIFEST_SANITIZED.json", names)
            self.assertNotIn("antigravity_rt_export.password.txt", names)


class TestRetrospectEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acf_test_retro_")
        self.old_env = os.environ.get("ACF_LOCK_DIR")
        os.environ["ACF_LOCK_DIR"] = self.tmp

    def tearDown(self):
        if self.old_env is not None:
            os.environ["ACF_LOCK_DIR"] = self.old_env
        else:
            os.environ.pop("ACF_LOCK_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_router_records_trajectory_and_retrospect_diagnoses(self):
        router = ActionRouter(DryRunBackend())
        act_ok = Action(
            action_id="r-ok",
            agent_id="agent-1",
            source="test",
            kind="click",
            target=Target(kind="window", pid=10, window_id=20),
            parameters={"x": 5, "y": 5},
        )
        router.execute(act_ok)

        act_fail = Action(
            action_id="r-fail",
            agent_id="agent-1",
            source="test",
            kind="click",
            target=Target(kind="window", pid=10, window_id=20),
        )
        record_trajectory(
            act_fail,
            BackendResult(status="failed", action_id="r-fail", backend="win32", details={"code": "point_outside_client"}),
            elapsed_ms=12.5,
        )

        rep = run_retrospect(limit=50)
        self.assertEqual(rep.total_actions, 2)
        self.assertEqual(rep.succeeded, 1)
        self.assertEqual(rep.failed, 1)
        self.assertEqual(rep.failure_codes.get("point_outside_client"), 1)
        self.assertTrue(any("point_outside_client" in f for f in rep.findings))


if __name__ == "__main__":
    unittest.main()
