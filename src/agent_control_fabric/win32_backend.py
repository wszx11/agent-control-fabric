"""Win32 backend: native window-level input routing via Windows user32.

Routes actions directly to window handles (HWND) using PostMessageW.
Background input is strictly enforced by default:
- click  -> PostMessage(hwnd, WM_LBUTTONDOWN/UP, ...) with client coordinates
- type   -> PostMessage(hwnd, WM_CHAR, ...) per character
- key    -> PostMessage(hwnd, WM_KEYDOWN/KEYUP, vk, ...)
- scroll -> PostMessage(hwnd, WM_MOUSEWHEEL, ...)

Does NOT move physical mouse cursor, does NOT switch foreground focus,
and checks window liveness and bounding rectangles before posting.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from .models import Action, BackendResult

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

def ensure_dpi_aware() -> None:
    """Enable Per-Monitor DPI awareness so window/UIA rects align on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

ensure_dpi_aware()

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wt.LPARAM)
user32.GetWindowTextW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)
user32.GetClassNameW.argtypes = (wt.HWND, wt.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.IsWindowEnabled.argtypes = (wt.HWND,)
user32.IsWindow.argtypes = (wt.HWND,)
user32.IsIconic.argtypes = (wt.HWND,)
user32.IsHungAppWindow.argtypes = (wt.HWND,)
user32.GetWindowRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
user32.GetForegroundWindow.argtypes = ()
user32.GetCursorPos.argtypes = (ctypes.POINTER(wt.POINT),)
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.PostMessageW.restype = wt.BOOL
user32.SendMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.SendMessageW.restype = wt.LPARAM

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
WHEEL_DELTA = 120

VK_MAP = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def _vk(key: str) -> Optional[int]:
    k = key.strip().lower()
    if k in VK_MAP:
        return VK_MAP[k]
    if len(k) == 1:
        return ord(k.upper())
    return None


def _lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    left: int
    top: int
    right: int
    bottom: int
    visible: bool
    enabled: bool
    foreground: bool


class Win32Backend:
    """Live Win32 background input routing backend."""

    name = "win32"

    def __init__(self, *, char_pacing: float = 0.005, allow_foreground: bool = False):
        self.char_pacing = char_pacing
        self.allow_foreground = allow_foreground

    @staticmethod
    def list_windows(title_substr: str = "", include_empty: bool = False) -> List[WindowInfo]:
        results: List[WindowInfo] = []
        fg = user32.GetForegroundWindow() or 0

        def _cb(hwnd: int, _lparam: Any) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if not n and not include_empty:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if title_substr and title_substr.lower() not in title.lower():
                return True
            rc = wt.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return True
            pid = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=title,
                    pid=int(pid.value),
                    left=int(rc.left),
                    top=int(rc.top),
                    right=int(rc.right),
                    bottom=int(rc.bottom),
                    visible=True,
                    enabled=bool(user32.IsWindowEnabled(hwnd)),
                    foreground=(int(hwnd) == int(fg)),
                )
            )
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        return results

    @classmethod
    def find_window(cls, title_substr: str) -> Optional[WindowInfo]:
        wins = cls.list_windows(title_substr)
        return wins[0] if wins else None

    @staticmethod
    def _client_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
        rc = wt.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        return (rc.left, rc.top, rc.right, rc.bottom)

    @staticmethod
    def _window_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(80)
        user32.GetClassNameW(hwnd, buf, 80)
        return buf.value

    @staticmethod
    def _restore_if_iconic_focus_safe(hwnd: int) -> bool:
        """Restore a minimized window (-32000/-25600) while preserving foreground focus."""
        if not user32.IsIconic(hwnd):
            return False
        fg = user32.GetForegroundWindow()
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if fg and fg != hwnd and user32.IsWindow(fg):
            user32.SetForegroundWindow(fg)
        time.sleep(0.15)
        return True

    def _check_target(self, hwnd: int) -> Optional[str]:
        if not user32.IsWindow(hwnd):
            return "window no longer exists (hwnd stale)"
        if not user32.IsWindowEnabled(hwnd):
            return "window is disabled"
        if hasattr(user32, "IsHungAppWindow") and user32.IsHungAppWindow(hwnd):
            return "window is hung (not responding to messages)"
        return None

    def execute(self, action: Action) -> BackendResult:
        if action.target.kind != "window":
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "target_not_supported", "message": "Win32 backend requires window target"},
            )

        if action.delivery_mode == "foreground" and not self.allow_foreground:
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={
                    "code": "foreground_not_allowed",
                    "message": "foreground delivery blocked by default to prevent desktop focus theft",
                },
            )

        kind = action.kind
        params = action.parameters

        if kind == "wait":
            secs = max(0.0, float(params.get("time", params.get("seconds", 0.5))))
            time.sleep(secs)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "wait", "waited_seconds": secs},
            )

        hwnd = action.target.window_id
        if not hwnd:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "missing_window_id", "message": "action target missing window_id"},
            )

        err = self._check_target(hwnd)
        if err:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "target_unavailable", "message": err, "hwnd": hwnd},
            )

        if kind in ("click", "double_click", "right_click", "middle_click"):
            restored = self._restore_if_iconic_focus_safe(hwnd)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            button = params.get("button", "right" if kind == "right_click" else "left")
            crect = self._client_rect(hwnd)
            if crect and (x < crect[0] or x > crect[2] or y < crect[1] or y > crect[3]):
                return BackendResult(
                    status="failed",
                    action_id=action.action_id,
                    backend=self.name,
                    details={
                        "code": "point_outside_client",
                        "message": f"click point ({x}, {y}) outside client rect ({crect[2]}x{crect[3]})",
                    },
                )
            lp = _lparam(x, y)
            mk = MK_LBUTTON if button == "left" else MK_RBUTTON
            wcls = self._window_class(hwnd)
            use_sync = bool(params.get("sync")) or (wcls == "H-SMILE-FRAME")
            send_fn = user32.SendMessageW if use_sync else user32.PostMessageW
            send_fn(hwnd, WM_MOUSEMOVE, 0, lp)
            if button == "right":
                send_fn(hwnd, WM_RBUTTONDOWN, mk, lp)
                send_fn(hwnd, WM_RBUTTONUP, 0, lp)
            elif kind == "double_click" or params.get("double"):
                send_fn(hwnd, WM_LBUTTONDOWN, mk, lp)
                send_fn(hwnd, WM_LBUTTONUP, 0, lp)
                send_fn(hwnd, WM_LBUTTONDBLCLK, mk, lp)
                send_fn(hwnd, WM_LBUTTONUP, 0, lp)
            else:
                send_fn(hwnd, WM_LBUTTONDOWN, mk, lp)
                send_fn(hwnd, WM_LBUTTONUP, 0, lp)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={
                    "tool": "click",
                    "x": x,
                    "y": y,
                    "button": button,
                    "hwnd": hwnd,
                    "dispatch": "SendMessageW" if use_sync else "PostMessageW",
                    "restored_iconic": restored,
                },
            )

        if kind == "type":
            text = str(params.get("text", ""))
            sent = 0
            for ch in text:
                code = 0x0D if ch == "\n" else ord(ch)
                if not user32.PostMessageW(hwnd, WM_CHAR, code, 0):
                    break
                sent += 1
                if self.char_pacing:
                    time.sleep(self.char_pacing)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "type", "chars_sent": sent, "hwnd": hwnd},
            )

        if kind in ("press", "key", "hotkey"):
            keys = params.get("keys", [params.get("key", "")])
            if isinstance(keys, str):
                keys = [keys]
            vks = [_vk(k) for k in keys if _vk(k) is not None]
            if not vks:
                return BackendResult(
                    status="failed",
                    action_id=action.action_id,
                    backend=self.name,
                    details={"code": "unknown_key", "keys": keys},
                )
            for vk in vks:
                user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1)
            for vk in reversed(vks):
                user32.PostMessageW(hwnd, WM_KEYUP, vk, 0xC0000001)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "key", "keys": keys, "hwnd": hwnd},
            )

        if kind == "scroll":
            direction = str(params.get("direction", "down")).lower()
            amount = int(params.get("amount", 3))
            step = WHEEL_DELTA if direction == "up" else -WHEEL_DELTA
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            lp = _lparam(x, y)
            for _ in range(max(1, amount)):
                user32.PostMessageW(hwnd, WM_MOUSEWHEEL, (step & 0xFFFF) << 16, lp)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "scroll", "direction": direction, "amount": amount, "hwnd": hwnd},
            )

        return BackendResult(
            status="failed",
            action_id=action.action_id,
            backend=self.name,
            details={"code": "unsupported_action_kind", "kind": kind},
        )
