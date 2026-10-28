# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.todesk — telemetry & sanitized bundle staging.

Pure-stdlib, read-only: no window operations, no focus changes.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import unittest

from agent_control_fabric import prepare_sanitized_rt_bundle
from agent_control_fabric.todesk import compute_sha256, get_todesk_telemetry


class TodeskTelemetryTests(unittest.TestCase):
    def test_telemetry_shape(self):
        t = get_todesk_telemetry()
        self.assertIsInstance(t.running, bool)
        self.assertIsInstance(t.pids, list)
        self.assertIsInstance(t.windows, list)
        self.assertIsInstance(t.installed, bool)


class SanitizedBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acf_rt_"))
        # valid payload files
        (self.tmp / "acct01.tar.enc").write_bytes(b"encrypted-payload-1")
        (self.tmp / "acct02.tar.enc").write_bytes(b"encrypted-payload-2")
        (self.tmp / "MANIFEST.sha256.txt").write_text("deadbeef  acct01.tar.enc\n")
        # must be rejected
        (self.tmp / "leak.password.txt").write_text("hunter2")
        (self.tmp / "secret.key").write_bytes(b"k" * 32)
        (self.tmp / "conf.env").write_text("TOKEN=abc")

    def test_bundle_excludes_plaintext_secrets(self):
        out = self.tmp / "bundle.zip"
        report = prepare_sanitized_rt_bundle(self.tmp, out)
        self.assertTrue(out.exists())
        self.assertGreaterEqual(report["included_count"], 3)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        joined = " ".join(names).lower()
        self.assertNotIn("password", joined)
        self.assertNotIn(".key", joined)
        self.assertNotIn(".env", joined)
        self.assertIn("MANIFEST_SANITIZED.json", names)

    def test_bundle_sha256_matches(self):
        out = self.tmp / "bundle2.zip"
        report = prepare_sanitized_rt_bundle(self.tmp, out)
        self.assertEqual(report["zip_sha256"], compute_sha256(out))

    def test_empty_dir_raises(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            prepare_sanitized_rt_bundle(empty, self.tmp / "should_not_exist.zip")


if __name__ == "__main__":
    unittest.main()
