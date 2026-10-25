"""Focus-safe parallel-screen window layout and user-activity guard.

Ensures multi-agent window operations never block or interrupt the local user:
1. `park_window_noactivate`: Places an agent-controlled window into a narrow
   side strip (or virtual monitor region) using `SWP_NOACTIVATE` so foreground
   focus stays untouched.
2. `UserInputGuard`: Samples OS `GetCursorPos` and `GetLastInputInfo` before
   and after actions to prove zero cursor teleportation, zero focus theft, and
   detect active human mouse movement.
"""

from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple

from .win32_backend import IS_WINDOWS, ensure_dpi_aware, user32, wt

ensure_dpi_aware()

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_BOTTOM = 1

if IS_WINDOWS:
    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.UINT),
            ("dwTime", wt.DWORD),
        ]

    user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
    user32.SetWindowPos.restype = wt.BOOL
    user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
    user32.GetLastInputInfo.restype = wt.BOOL
    kernel32.GetTickCount.argtypes = ()
    kernel32.GetTickCount.restype = wt.DWORD
else:
    kernel32 = None


# Standard 1920x1080 parallel-screen non-intrusive parking slots
PARKING_SLOTS: Dict[str, Tuple[int, int, int, int]] = {
    "right-strip": (1460, 0, 450, 882),
    "left-strip": (30, 0, 450, 882),
    "bottom-right": (1260, 480, 640, 520),
}


@dataclass
class InputSnapshot:
    foreground_hwnd: int
    cursor_x: int
    cursor_y: int
    user_idle_ms: int


def sample_input_state() -> InputSnapshot:
    if not IS_WINDOWS or user32 is None:
        return InputSnapshot(foreground_hwnd=0, cursor_x=0, cursor_y=0, user_idle_ms=0)
    fg = int(user32.GetForegroundWindow() or 0)
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    idle_ms = 0
    if user32.GetLastInputInfo(ctypes.byref(lii)):
        now_tick = int(kernel32.GetTickCount())
        idle_ms = (now_tick - int(lii.dwTime)) & 0xFFFFFFFF
    return InputSnapshot(
        foreground_hwnd=fg,
        cursor_x=int(pt.x),
        cursor_y=int(pt.y),
        user_idle_ms=int(idle_ms),
    )


class UserInputGuard:
    """Context/helper verifying that background actions neither steal focus nor move the cursor."""

    def __init__(self, *, restore_foreground_on_drift: bool = True):
        self.restore_foreground_on_drift = restore_foreground_on_drift
        self.before: InputSnapshot | None = None
        self.after: InputSnapshot | None = None

    def __enter__(self) -> "UserInputGuard":
        self.before = sample_input_state()
        return self

    def __exit__(self, *exc: object) -> bool:
        self.after = sample_input_state()
        if (
            IS_WINDOWS
            and user32 is not None
            and self.restore_foreground_on_drift
            and self.before
            and self.before.foreground_hwnd
            and self.after.foreground_hwnd
            and self.after.foreground_hwnd != self.before.foreground_hwnd
            and user32.IsWindow(self.after.foreground_hwnd)
        ):
            # Yield activation back to the user's window without calling SetForegroundWindow:
            # minimize the drifted background window briefly, then restore it with SW_SHOWNOACTIVATE(4) at HWND_BOTTOM.
            drifted_hwnd = int(self.after.foreground_hwnd)
            rc = wt.RECT()
            user32.GetWindowRect(drifted_hwnd, ctypes.byref(rc))
            w = max(300, int(rc.right - rc.left))
            h = max(200, int(rc.bottom - rc.top))
            user32.ShowWindow(drifted_hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.15)
            user32.ShowWindow(drifted_hwnd, 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(drifted_hwnd, HWND_BOTTOM, int(rc.left), int(rc.top), w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.after = sample_input_state()
        return False

    def summary(self) -> dict[str, object]:
        if not self.before or not self.after:
            return {"checked": False}
        return {
            "checked": True,
            "focus_preserved": self.before.foreground_hwnd == self.after.foreground_hwnd,
            "cursor_unchanged": (self.before.cursor_x, self.before.cursor_y)
            == (self.after.cursor_x, self.after.cursor_y),
            "before": asdict(self.before),
            "after": asdict(self.after),
        }


def park_window_noactivate(
    hwnd: int,
    slot: str = "right-strip",
    *,
    rect: Tuple[int, int, int, int] | None = None,
    send_to_bottom: bool = True,
) -> dict[str, object]:
    """Reposition `hwnd` into a non-intrusive parking slot without activating it."""
    if not user32.IsWindow(hwnd):
        return {"ok": False, "hwnd": hwnd, "error": "invalid window handle"}

    x, y, w, h = rect if rect is not None else PARKING_SLOTS.get(slot, PARKING_SLOTS["right-strip"])
    with UserInputGuard() as guard:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW | (0 if send_to_bottom else SWP_NOZORDER)
        z_order = HWND_BOTTOM if send_to_bottom else 0
        ok = bool(user32.SetWindowPos(hwnd, z_order, int(x), int(y), int(w), int(h), flags))
        rc = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))

    return {
        "ok": ok,
        "hwnd": hwnd,
        "slot": slot,
        "actual_rect": (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)),
        "guard": guard.summary(),
    }
