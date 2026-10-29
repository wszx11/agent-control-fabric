# -*- coding: utf-8 -*-
"""Real-window coverage for win32 action kinds that had none:
scroll / drag / hotkey / middle_click, plus window enumeration.

The test window lives on its own thread running a GetMessage pump — without a
pump the window is reported as hung by SendMessageTimeout, which would make the
backend look broken when the fixture is what's broken.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
import unittest
from pathlib import Path

# Resolve the package from this file instead of an absolute path, so the suite is
# portable across machines and CI images.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_control_fabric import Action, Target, Win32Backend  # noqa: E402

IS_WINDOWS = sys.platform == "win32"

# ctypes.wintypes / WINFUNCTYPE / windll only exist on Windows; mirror the library
# convention (import guarded, handle set to None off-Windows) so discovery does
# not explode on other platforms.
if IS_WINDOWS:
    try:
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except (ImportError, AttributeError, OSError):
        IS_WINDOWS = False

if not IS_WINDOWS:
    wt = None  # type: ignore[assignment]
    user32 = None
    kernel32 = None

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WM_QUIT = 0x00CF0000, 0x10000000, 0x0012

if IS_WINDOWS:
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    user32.DefWindowProcW.restype = ctypes.c_longlong
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]


class _PumpedWindow(threading.Thread):
    """Owns a top-level window and pumps its message queue until stopped."""

    daemon = True

    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.hwnd: int | None = None
        self.ready = threading.Event()
        self._class = f"AcfKindsWC{id(self)}"
        self._wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
        self._tid: int | None = None

    def run(self) -> None:
        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE,
                                           wt.LPVOID]
        self._tid = kernel32.GetCurrentThreadId()

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = self._class
        wc.hInstance = kernel32.GetModuleHandleW(None)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self._class, self.title,
                                           WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                           20, 20, 320, 220, None, None,
                                           wc.hInstance, None)
        self.ready.set()
        if not self.hwnd:
            return

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterClassW(self._class, wc.hInstance)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.join(timeout=3)


@unittest.skipUnless(IS_WINDOWS, "win32 backend requires Windows")
class Win32UntestedKindsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = _PumpedWindow("acf-w32-kinds")
        cls.win.start()
        cls.win.ready.wait(timeout=5)
        assert cls.win.hwnd, "test window creation failed"
        cls.backend = Win32Backend()

    @classmethod
    def tearDownClass(cls):
        cls.win.stop()

    def _run(self, kind, params):
        act = Action(action_id=f"k-{kind}", agent_id="t", source="semantic", kind=kind,
                     target=Target(kind="window", pid=1, window_id=self.win.hwnd),
                     parameters=params, delivery_mode="background")
        t0 = time.perf_counter()
        res = self.backend.execute(act)
        ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(res.status, "executed", f"{kind}: {res.details}")
        self.assertLess(ms, 2000, f"{kind} too slow: {ms:.0f}ms")

    def test_scroll_vertical_and_horizontal(self):
        for direction in ("down", "up", "left", "right"):
            self._run("scroll", {"direction": direction, "amount": 2, "by": "line"})

    def test_drag_interpolated(self):
        self._run("drag", {"from_x": 30, "from_y": 30, "to_x": 200, "to_y": 150,
                           "button": "left", "duration_ms": 60, "steps": 4})

    def test_hotkey_two_keys(self):
        self._run("hotkey", {"keys": ["ctrl", "a"]})

    def test_middle_click(self):
        self._run("middle_click", {"x": 100, "y": 100})

    def test_window_is_enumerable(self):
        titles = [w.title for w in Win32Backend.list_windows("")]
        self.assertTrue(any(self.win.title in (t or "") for t in titles),
                        f"test window not enumerated; sample={titles[:5]}")


if __name__ == "__main__":
    unittest.main()
