# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.virtual_desktop — v0.5 isolation engine.

Pure-stdlib behavioral tests; Windows-only paths are skipped elsewhere.
No real user windows are touched; only private window classes created and
destroyed inside this test.
"""
from __future__ import annotations

import ctypes
import unittest

from agent_control_fabric.virtual_desktop import (
    IS_WINDOWS,
    VirtualDesktop,
    VirtualDesktopError,
    run_on_virtual_desktop,
)


@unittest.skipUnless(IS_WINDOWS, "virtual desktops require Windows")
class VirtualDesktopTests(unittest.TestCase):
    def test_open_or_create_and_close(self):
        vd = VirtualDesktop("acf-test-vd-open")
        h = vd.open_or_create()
        self.assertTrue(h)
        vd.close()
        self.assertIsNone(vd.handle)

    def test_list_desktops_contains_default(self):
        names = VirtualDesktop.list_desktops()
        self.assertTrue(any("Default" in n for n in names))

    def test_run_on_virtual_desktop_roundtrip(self):
        vd = VirtualDesktop("acf-test-vd-run")
        with vd:
            def work():
                return VirtualDesktop.current_input_desktop()
            # must not raise; affinity switched and restored
            name = run_on_virtual_desktop(vd, work)
            self.assertIsInstance(name, str)

    def test_non_windows_raises(self):
        if IS_WINDOWS:
            self.skipTest("only meaningful off-Windows")
        with self.assertRaises(VirtualDesktopError):
            VirtualDesktop("x").open_or_create()


if __name__ == "__main__":
    unittest.main()
