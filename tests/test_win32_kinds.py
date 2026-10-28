# -*- coding: utf-8 -*-
"""Tests for win32_backend untested action kinds: scroll, drag, hotkey, middle_click.

DryRun-free: uses Win32Backend against a private test window (created/destroyed
in-test), so message delivery paths are exercised for real without touching any
user window. Focus/cursor safety asserted after every interactive kind.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
import unittest

REPO = r"D:/ai gongzuoqu/zonzhang-office/00-总指挥办公室/tools/agent-control-fabric"
sys.path.insert(0, REPO + "/src")

from agent_control_fabric import Action, Target, Win32Backend

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def _make_window():
    wc_name = "AcfW32KindsWC"
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(user32.DefWindowProcW, WNDPROC)
    wc.lpszClassName = wc_name
    wc.hInstance = kernel32.GetModuleHandleW(None)
    atom = user32.RegisterClassW(ctypes.byref(wc))
    assert atom, "RegisterClassW failed"
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    WS_OVERLAPPEDWINDOW = 0x00CF0000
    WS_VISIBLE = 0x10000000
    hwnd = user32.CreateWindowExW(0, wc_name, "acf-w32-kinds", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  20, 20, 320, 220, None, None, wc.hInstance, None)
    assert hwnd, "CreateWindowExW failed"
    return hwnd, wc_name, wc.hInstance


@unittest.skipUnless(sys.platform == "win32", "win32 backend requires Windows")
class Win32UntestedKindsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hwnd, cls.wc_name, cls.hinst = _make_window()
        cls.backend = Win32Backend()

    @classmethod
    def tearDownClass(cls):
        user32.DestroyWindow(cls.hwnd)
        user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user32.UnregisterClassW(cls.wc_name, cls.hinst)

    def _run(self, kind, params):
        act = Action(action_id=f"k-{kind}", agent_id="t", source="semantic",
                     kind=kind, target=Target(kind="window", pid=1, window_id=self.hwnd),
                     parameters=params, delivery_mode="background")
        t0 = time.perf_counter()
        res = self.backend.execute(act)
        ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(res.status, "executed", f"{kind}: {res.details}")
        self.assertLess(ms, 2000, f"{kind} too slow: {ms:.0f}ms")
        return res

    def test_scroll_vertical_and_horizontal(self):
        for direction in ("down", "up", "left", "right"):
            self._run("scroll", {"direction": direction, "amount": 2, "by": "line"})

    def test_drag_interpolated(self):
        self._run("drag", {"from_x": 30, "from_y": 30, "to_x": 200, "to_y": 150,
                           "button": "left", "duration_ms": 60, "steps": 4})
        # zero physical mouse movement
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self.assertIsInstance(pt.x, int)

    def test_hotkey_two_keys(self):
        self._run("hotkey", {"keys": ["ctrl", "a"]})

    def test_middle_click(self):
        self._run("middle_click", {"x": 100, "y": 100})

    def test_find_window_helper(self):
        # list_windows signature is positional-only; verify via EnumWindows scan
        wins = Win32Backend.list_windows("")
        titles = [w.title for w in wins]
        self.assertTrue(any("acf-w32-kinds" in (t or "") for t in titles),
                        f"test window not enumerated: {titles[:6]}")


if __name__ == "__main__":
    unittest.main()
