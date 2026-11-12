# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.retrospect — trajectory recording & analysis.

All classes isolate ACF_LOCK_DIR into a private temp dir so no fixture record
can ever reach the production `_locks/trajectories.jsonl`.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_control_fabric import Action, BackendResult, Target
from agent_control_fabric.retrospect import (
    is_test_record,
    record_trajectory,
    run_retrospect,
    trajectory_path,
)

try:
    from ._isolation import IsolatedLockDirMixin
except ImportError:  # direct script execution
    from _isolation import IsolatedLockDirMixin


def _write_raw(record: dict) -> None:
    p = Path(trajectory_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fixture_record(agent_id: str = "router-test", source: str = "semantic",
                    status: str = "executed", elapsed_ms: float = 1.0) -> dict:
    return {"ts": 0.0, "action_id": "fx", "agent_id": agent_id, "source": source,
            "kind": "wait", "status": status, "elapsed_ms": elapsed_ms, "code": None}


def _real_record(agent_id: str = "acf-daemon-cron", source: str = "acf-duty",
                 status: str = "executed", elapsed_ms: float = 10.0,
                 code: str | None = None) -> dict:
    return {"ts": 0.0, "action_id": "real", "agent_id": agent_id, "source": source,
            "kind": "click", "status": status, "elapsed_ms": elapsed_ms, "code": code}


class RetrospectRecordTests(IsolatedLockDirMixin, unittest.TestCase):
    def test_record_trajectory_appends_atomic(self):
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


class RetrospectRunTests(IsolatedLockDirMixin, unittest.TestCase):
    def test_run_retrospect_returns_report(self):
        rep = run_retrospect(limit=50)
        self.assertIsNotNone(rep)
        self.assertGreaterEqual(rep.total_actions, 0)
        self.assertIsInstance(rep.findings, list)
        self.assertIsInstance(rep.next_iterations, list)
        self.assertTrue(0.0 <= rep.success_rate <= 1.0)


class IsTestRecordTests(unittest.TestCase):
    def test_known_fixture_ids_are_test_records(self):
        for agent_id in ("router-test", "agent-A", "agent-B", "agent-baseline",
                         "agent-unknown", "test-agent", "a", "b", "t"):
            self.assertTrue(is_test_record({"agent_id": agent_id, "source": "semantic"}),
                            f"{agent_id} should classify as a test fixture")

    def test_real_business_ids_are_not_test_records(self):
        for agent_id, source in (("acf-daemon-cron", "acf-duty"),
                                 ("hm-desk-02", "acf-v04"),
                                 ("acf-cloudpc", "acf-cloudpc"),
                                 ("HM-DESK-02-A", "desktop_audit")):
            self.assertFalse(is_test_record({"agent_id": agent_id, "source": source}),
                             f"{agent_id}/{source} should classify as real business")

    def test_unit_source_is_test_record(self):
        self.assertTrue(is_test_record({"agent_id": "whatever", "source": "unit"}))


class FixtureFilteringTests(IsolatedLockDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # 5 fixtures + 3 real business records (1 failed)
        for i in range(5):
            _write_raw(_fixture_record(agent_id="router-test", elapsed_ms=1.0))
        _write_raw(_real_record(status="executed", elapsed_ms=10.0))
        _write_raw(_real_record(status="executed", elapsed_ms=20.0))
        _write_raw(_real_record(status="failed", elapsed_ms=5.0,
                                code="target_unavailable"))

    def test_fixtures_excluded_and_real_stats_recomputed(self):
        rep = run_retrospect(limit=200)
        self.assertEqual(rep.total_actions, 3)
        self.assertEqual(rep.real_records_analyzed, 3)
        self.assertEqual(rep.excluded_test_records, 5)
        self.assertEqual(rep.succeeded, 2)
        self.assertEqual(rep.failed, 1)
        self.assertEqual(rep.success_rate, round(2 / 3, 4))
        self.assertEqual(rep.avg_latency_ms, round((10.0 + 20.0 + 5.0) / 3, 2))
        self.assertEqual(rep.max_latency_ms, 20.0)
        self.assertEqual(rep.failure_codes.get("target_unavailable"), 1)
        self.assertTrue(any("已排除 5 条测试夹具" in f for f in rep.findings))

    def test_include_test_records_restores_raw_window(self):
        rep = run_retrospect(limit=200, include_test_records=True)
        self.assertEqual(rep.total_actions, 8)
        self.assertEqual(rep.excluded_test_records, 0)
        self.assertEqual(rep.succeeded, 7)

    def test_to_dict_carries_audit_fields(self):
        d = run_retrospect(limit=200).to_dict()
        self.assertIn("excluded_test_records", d)
        self.assertIn("real_records_analyzed", d)
        self.assertEqual(d["excluded_test_records"], 5)


class AllFixtureWindowTests(IsolatedLockDirMixin, unittest.TestCase):
    def test_no_healthy_false_positive_when_window_is_all_fixtures(self):
        for _ in range(4):
            _write_raw(_fixture_record(agent_id="agent-A", source="dogfood-d"))
        rep = run_retrospect(limit=200)
        self.assertEqual(rep.total_actions, 0)
        self.assertEqual(rep.real_records_analyzed, 0)
        self.assertEqual(rep.excluded_test_records, 4)
        self.assertEqual(rep.success_rate, 0.0)
        joined = "\n".join(rep.findings)
        self.assertNotIn("全部健康", joined)
        self.assertIn("无真实业务轨迹", joined)
        self.assertIn("已排除 4 条测试夹具记录", joined)

    def test_empty_log_reports_no_records(self):
        rep = run_retrospect(limit=200)
        self.assertEqual(rep.total_actions, 0)
        self.assertEqual(rep.excluded_test_records, 0)
        self.assertIn("尚无轨迹记录", "\n".join(rep.findings))


if __name__ == "__main__":
    unittest.main()
