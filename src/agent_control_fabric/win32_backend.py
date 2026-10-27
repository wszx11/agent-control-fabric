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
import os
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from .models import Action, BackendResult

IS_WINDOWS = os.name == "nt"
if IS_WINDOWS:
    import ctypes.wintypes as wt
    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    gdi32: Any = ctypes.WinDLL("gdi32", use_last_error=True)
else:
    wt = None  # type: ignore[assignment]
    user32 = None
    gdi32 = None


def ensure_dpi_aware() -> None:
    """Enable Per-Monitor DPI awareness so window/UIA rects align on scaled displays."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


if IS_WINDOWS:
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
    user32.SendMessageTimeoutW.argtypes = (
        wt.HWND,
        wt.UINT,
        wt.WPARAM,
        wt.LPARAM,
        wt.UINT,
        wt.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    )
    user32.SendMessageTimeoutW.restype = wt.LPARAM
else:
    WNDENUMPROC = None

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010
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



# --- User-document protection guard (v0.5.1) -------------------------------
# Windows where a human is actively editing real content (Notepad/Word/Excel...)
# must NEVER receive injected keys or clicks, even in background mode.
import re as _re
_USER_DOC_PAT = _re.compile(
    r"\*\s*[^\r\n]*\.(txt|md|docx?|xlsx?|pptx?|csv|log|json|py|ps1|bat|ini|cfg)\s*[-—]\s*", _re.I)
_INTERACTIVE_APPS = ("notepad", "word", "excel", "powerpnt", "wps", "notepad\+\+")

def is_user_document_window(title: str, process_name: str = "") -> bool:
    """True if the window looks like a human-edited document (typed keys would corrupt it)."""
    if not title:
        return False
    if _USER_DOC_PAT.search(title):
        return True
    pl = (process_name or "").lower()
    return any(a in pl or a in title.lower() for a in _INTERACTIVE_APPS if a != "notepad\+\+" or True)


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
    def _window_title(hwnd: int) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    @staticmethod
    def _window_class(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(80)
        user32.GetClassNameW(hwnd, buf, 80)
        return buf.value

    @staticmethod
    def _restore_if_iconic_focus_safe(hwnd: int) -> bool:
        """Restore a minimized window (-32000/-25600) via SW_SHOWNOACTIVATE without stealing focus.

        Adaptive poll instead of fixed 100ms sleep: un-minimizing typically completes
        in 10-30ms, so we poll IsIconic every 10ms (max 150ms) and return early.
        Saves ~70-90ms on every restore-then-click path (retrospect slow-action fix).
        """
        if not user32.IsIconic(hwnd):
            return False
        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE (never activates or steals foreground)
        deadline = time.perf_counter() + 0.15
        while time.perf_counter() < deadline:
            if not user32.IsIconic(hwnd):
                break
            time.sleep(0.01)
        return True

    def _check_target(self, hwnd: int) -> Optional[str]:
        if not IS_WINDOWS or user32 is None:
            return "Win32Backend requires Windows OS"
        if not user32.IsWindow(hwnd):
            return "window no longer exists (hwnd stale)"
        if not user32.IsWindowEnabled(hwnd):
            return "window is disabled"
        if hasattr(user32, "IsHungAppWindow") and user32.IsHungAppWindow(hwnd):
            return "window is hung (not responding to messages)"
        # WM_NULL (0x0000) liveness heartbeat with SMTO_ABORTIFHUNG (0x0002), 1500ms timeout
        out_res = ctypes.c_size_t(0)
        if not user32.SendMessageTimeoutW(hwnd, 0x0000, 0, 0, 0x0002, 1500, ctypes.byref(out_res)):
            return "window message pump timed out (WM_NULL 1500ms)"
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

        if kind in ("press", "type", "click", "double_click", "right_click"):
            title = self._window_title(hwnd)
            if is_user_document_window(title):
                return BackendResult(
                    status="blocked",
                    action_id=action.action_id,
                    backend=self.name,
                    details={"code": "user_document_protected",
                             "message": f"window {hwnd} looks like a human-edited document; injected {kind} forbidden"},
                )

        if kind in ("click", "double_click", "right_click", "middle_click"):
            restored = self._restore_if_iconic_focus_safe(hwnd)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            default_btn = "right" if kind == "right_click" else ("middle" if kind == "middle_click" else "left")
            button = str(params.get("button", default_btn)).lower()
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
            mk = MK_RBUTTON if button == "right" else (MK_MBUTTON if button == "middle" else MK_LBUTTON)
            wcls = self._window_class(hwnd)
            use_sync = bool(params.get("sync")) or (wcls == "H-SMILE-FRAME")
            send_fn = user32.SendMessageW if use_sync else user32.PostMessageW
            if use_sync:
                # Sciter / custom-drawn windows require WM_MOUSEACTIVATE + WM_SETCURSOR + WM_MOUSEMOVE hover update
                send_fn(hwnd, 0x0021, hwnd, (WM_LBUTTONDOWN << 16) | 1)
                send_fn(hwnd, 0x0020, hwnd, (WM_MOUSEMOVE << 16) | 1)
            send_fn(hwnd, WM_MOUSEMOVE, 0, lp)
            if use_sync:
                send_fn(hwnd, 0x0020, hwnd, (WM_LBUTTONDOWN << 16) | 1)
            if button == "right":
                send_fn(hwnd, WM_RBUTTONDOWN, mk, lp)
                send_fn(hwnd, WM_RBUTTONUP, 0, lp)
            elif button == "middle":
                send_fn(hwnd, WM_MBUTTONDOWN, mk, lp)
                send_fn(hwnd, WM_MBUTTONUP, 0, lp)
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
            text = str(params.get("text", params.get("content", "")))
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
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            lp = _lparam(x, y)
            if direction in ("left", "right"):
                msg = WM_MOUSEHWHEEL
                step = WHEEL_DELTA if direction == "right" else -WHEEL_DELTA
            else:
                msg = WM_MOUSEWHEEL
                step = WHEEL_DELTA if direction == "up" else -WHEEL_DELTA
            for _ in range(max(1, amount)):
                user32.PostMessageW(hwnd, msg, (step & 0xFFFF) << 16, lp)
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "scroll", "direction": direction, "amount": amount, "hwnd": hwnd},
            )

        if kind == "drag":
            fx = int(params.get("from_x", params.get("x", 0)))
            fy = int(params.get("from_y", params.get("y", 0)))
            tx = int(params.get("to_x", fx))
            ty = int(params.get("to_y", fy))
            steps = max(2, int(params.get("steps", 8)))
            lp_start = _lparam(fx, fy)
            user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp_start)
            user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp_start)
            for i in range(1, steps + 1):
                ix = int(round(fx + (tx - fx) * i / steps))
                iy = int(round(fy + (ty - fy) * i / steps))
                user32.PostMessageW(hwnd, WM_MOUSEMOVE, MK_LBUTTON, _lparam(ix, iy))
                time.sleep(0.005)
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, _lparam(tx, ty))
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "drag", "from": (fx, fy), "to": (tx, ty), "steps": steps, "hwnd": hwnd},
            )

        return BackendResult(
            status="failed",
            action_id=action.action_id,
            backend=self.name,
            details={"code": "unsupported_action_kind", "kind": kind},
        )
