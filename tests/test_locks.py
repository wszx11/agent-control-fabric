# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.locks — cross-process ResourceLock semantics."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_control_fabric.locks import ResourceLock


class ResourceLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acf_locks_")

    def test_acquire_release_roundtrip(self):
        import uuid
        lock = ResourceLock(f"test-key-1-{uuid.uuid4().hex[:6]}", agent="t", ttl=30)
        lock.acquire(timeout=2)
        self.assertTrue(lock._held)
        lock.release()
        self.assertFalse(lock._held)

    def test_mutual_exclusion_same_key(self):
        import threading
        lock_a = ResourceLock("test-key-2", agent="a", ttl=30)
        lock_b = ResourceLock("test-key-2", agent="b", ttl=30)
        lock_a.acquire(timeout=2)
        with self.assertRaises(Exception):
            lock_b.acquire(timeout=0.3)  # rejected while A holds it
        lock_a.release()
        lock_b.acquire(timeout=2)  # free after release
        lock_b.release()

    def test_different_keys_do_not_block(self):
        lock_a = ResourceLock("test-key-3a", agent="a", ttl=30)
        lock_b = ResourceLock("test-key-3b", agent="b", ttl=30)
        lock_a.acquire(timeout=2)
        lock_b.acquire(timeout=2)
        lock_a.release()
        lock_b.release()


if __name__ == "__main__":
    unittest.main()
