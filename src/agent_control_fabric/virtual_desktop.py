# -*- coding: utf-8 -*-
"""ACF v0.5 development: virtual_desktop.py — CreateDesktop true isolation.

Agent workloads run on a separate Windows desktop object; the human's desktop
(visible one) is never touched. Zero focus theft by construction: we never call
SwitchDesktop to the agent desktop while the human works, and never steal input.

Design (kept minimal & stdlib-only, consistent with ACF house style):
- VirtualDesktop: create/open a named desktop, list existing ones.
- virtual_window_loop: run a callable (e.g. window creation + PrintWindow capture)
  with thread affinity switched to the agent desktop, then switch back. Windows
  created there live only on that desktop — invisible from the human desktop.
- All raw ctypes guarded for non-Windows import safety (module imports clean anywhere).

Redlines honored: no SetForegroundWindow, no mouse movement, no SW_RESTORE on the
user's windows. Thread affinity switching (SetThreadDesktop) only affects OUR thread.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")

import sys as _sys
IS_WINDOWS = _sys.platform == "win32"


if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    RIGHTS_DESKTOP_ALL = (
        0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080 | 0x0100 | 0x0200 | 0x10000
    )  # GENERIC_READ|WRITE|EXECUTE|ALL for DESKTOP objects (composite)
else:
    user32 = None
    kernel32 = None
    RIGHTS_DESKTOP_ALL = 0


@dataclass
class DesktopInfo:
    name: str
    handle: Optional[int]
    is_input_desktop: bool


class VirtualDesktopError(RuntimeError):
    pass


class VirtualDesktop:
    """A named Windows desktop object for isolating agent-side windows."""

    def __init__(self, name: str = "acf-agent-desktop") -> None:
        self.name = name
        self._handle: Optional[int] = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def handle(self) -> Optional[int]:
        return self._handle

    def open_or_create(self) -> int:
        """Open the named desktop, creating it if absent. Returns the HDESK."""
        if not IS_WINDOWS:
            raise VirtualDesktopError("virtual desktops require Windows")
        if self._handle:
            return self._handle
        h = user32.OpenDesktopW(self.name, 0, False, RIGHTS_DESKTOP_ALL)
        if not h:
            h = user32.CreateDesktopW(self.name, None, None, 0, RIGHTS_DESKTOP_ALL, None)
        if not h:
            raise VirtualDesktopError(f"OpenDesktopW/CreateDesktopW failed for {self.name!r}")
        self._handle = h
        return self._handle

    def close(self) -> None:
        if self._handle:
            user32.CloseDesktop(self._handle)
            self._handle = None

    def __enter__(self) -> "VirtualDesktop":
        self.open_or_create()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- info --------------------------------------------------------------
    @staticmethod
    def current_input_desktop() -> str:
        if not IS_WINDOWS:
            raise VirtualDesktopError("virtual desktops require Windows")
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetUserObjectInformationW(
            user32.GetThreadDesktop(kernel32.GetCurrentThreadId()), 2, buf, 256, None
        )  # UOI_NAME = 2
        return buf.value if n else "<unknown>"

    @staticmethod
    def list_desktops() -> List[str]:
        """List desktops in the current window station (best-effort)."""
        if not IS_WINDOWS:
            return []
        found: List[str] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_wchar_p, ctypes.c_void_p)
        def cb(name, _lparam):
            found.append(name)
            return True

        hwinsta = user32.GetProcessWindowStation()
        user32.EnumDesktopsW(hwinsta, cb, None)
        return found


def run_on_virtual_desktop(desktop: VirtualDesktop, work: Callable[[], T]) -> T:
    """Run `work` with the calling thread attached to the virtual desktop.

    The human's (input) desktop is never switched away from — this only changes
    OUR thread's desktop affinity, so any windows/DCs created by `work` belong
    to the isolated desktop. Restores the previous affinity in `finally`.
    """
    hdesk = desktop.open_or_create()
    old = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    if not old:
        raise VirtualDesktopError("GetThreadDesktop failed")
    ok = user32.SetThreadDesktop(hdesk)
    if not ok:
        raise VirtualDesktopError("SetThreadDesktop failed (thread may own windows)")
    try:
        return work()
    finally:
        user32.SetThreadDesktop(old)
