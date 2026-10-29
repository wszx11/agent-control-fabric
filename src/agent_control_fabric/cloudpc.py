"""Dual-track Cloud PC controller (A2A command channel + ToDesk offscreen GUI).

Manages the remote Cloud PC (e.g. a ToDesk remote host)
without ever blocking or interrupting the local user's mouse or keyboard:
- Track A (Preferred): Enqueues commands into `cloudpc_a2a/inbox.jsonl` or
  delivers sanitized bundles via ToDesk File Transfer in the background.
- Track B (Fallback GUI & Router): Captures the Cloud PC screen offscreen via
  `PrintWindow` or `cua-driver` / `cloudpc_peek`, and sends window-local
  background mouse/keyboard messages through ActionRouter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .backends import Backend, CompositeBackend, CuaDriverBackend, DryRunBackend
from .capture import CaptureResult, capture_window_offscreen
from .layout import UserInputGuard
from .models import Action, Target
from .router import ActionRouter
from .todesk import get_todesk_telemetry
from .win32_backend import Win32Backend

CST = timezone(timedelta(hours=8))
DEFAULT_A2A_DIR = Path(os.environ.get("ACF_A2A_DIR", str(Path.home() / ".acf" / "cloudpc_a2a")))
DEFAULT_CUA_DRIVER = r"C:\Users\zx\AppData\Local\Programs\Cua\cua-driver\bin\cua-driver.EXE"
FULLSCREEN_MIN_W = 800


def resolve_cua_driver_path(custom_path: Optional[str] = None) -> str:
    """Return an available path to cua-driver executable."""
    if custom_path and os.path.exists(custom_path):
        return custom_path
    which_path = shutil.which("cua-driver") or shutil.which("cua-driver.exe")
    if which_path:
        return which_path
    if os.path.exists(DEFAULT_CUA_DRIVER):
        return DEFAULT_CUA_DRIVER
    return "cua-driver"


def call_cua_driver_tool(
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    executable: Optional[str] = None,
    timeout: float = 30.0,
    retries: int = 3,
) -> Optional[Any]:
    """Invoke cua-driver tool with retry semantics, returning parsed JSON or None."""
    driver = resolve_cua_driver_path(executable)
    cmd = [driver, "call", tool]
    if args:
        cmd += ["--args", json.dumps(args, ensure_ascii=False)]
    for attempt in range(max(1, retries)):
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            raw = (p.stdout or "").strip()
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(1.0 * (attempt + 1))
    return None


def find_cloudpc_session_window(
    *,
    min_width: int = FULLSCREEN_MIN_W,
    use_cua: bool = True,
    driver_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Discover active ToDesk Cloud PC remote session window.

    Bridges cloudpc_peek's area/geometry heuristics and cua-driver process/window
    discovery with ACF Win32 telemetry.
    """
    # 1. Fast Win32 telemetry check
    telem = get_todesk_telemetry()
    if telem.remote_window_id and telem.pids:
        for w in telem.windows:
            if w.hwnd == telem.remote_window_id:
                w_width = w.rect[2] - w.rect[0]
                w_height = w.rect[3] - w.rect[1]
                if w.rect[0] > -30000 and w.rect[1] > -30000 and w_width >= min_width:
                    return {
                        "pid": w.pid,
                        "window_id": w.hwnd,
                        "title": w.title,
                        "x": w.rect[0],
                        "y": w.rect[1],
                        "width": w_width,
                        "height": w_height,
                        "area": w_width * w_height,
                        "source": "win32_telemetry",
                    }

    # Also scan any visible ToDesk window with area > 0 and width >= min_width
    best_win32 = None
    for w in telem.windows:
        if not w.visible:
            continue
        w_width = w.rect[2] - w.rect[0]
        w_height = w.rect[3] - w.rect[1]
        if w.rect[0] <= -30000 or w.rect[1] <= -30000 or w_width < min_width:
            continue
        area = w_width * w_height
        if best_win32 is None or area > best_win32["area"]:
            best_win32 = {
                "pid": w.pid,
                "window_id": w.hwnd,
                "title": w.title,
                "x": w.rect[0],
                "y": w.rect[1],
                "width": w_width,
                "height": w_height,
                "area": area,
                "source": "win32_enum",
            }
    if best_win32:
        return best_win32

    # 2. Cua Driver tree exploration (cloudpc_peek heuristic)
    if use_cua:
        tree = call_cua_driver_tool("get_accessibility_tree", executable=driver_path) or {}
        pids: Set[int] = {
            p.get("pid")
            for p in tree.get("processes") or []
            if "todesk" in str(p.get("name") or "").lower()
        }
        if telem.pids:
            pids.update(telem.pids)

        best_cua = None
        for w in tree.get("windows") or []:
            if w.get("pid") not in pids:
                continue
            x, y = w.get("x") or 0, w.get("y") or 0
            wd, ht = w.get("width") or 0, w.get("height") or 0
            if x <= -30000 or y <= -30000 or wd < min_width:
                continue
            area = wd * ht
            if best_cua is None or area > best_cua["area"]:
                best_cua = {
                    "pid": w.get("pid"),
                    "window_id": w.get("window_id"),
                    "title": w.get("title", ""),
                    "x": x,
                    "y": y,
                    "width": wd,
                    "height": ht,
                    "area": area,
                    "source": "cua_driver",
                }
        if best_cua:
            return best_cua

    return None


def cloudpc_status(a2a_dir: str | Path = DEFAULT_A2A_DIR) -> Dict[str, Any]:
    """Return combined status of ToDesk remote window and Cloud PC A2A queues."""
    telem = get_todesk_telemetry()
    base = Path(a2a_dir)
    inbox = base / "inbox.jsonl"
    outbox = base / "outbox.jsonl"
    inbox_lines = len(inbox.read_text(encoding="utf-8", errors="replace").splitlines()) if inbox.exists() else 0
    outbox_lines = len(outbox.read_text(encoding="utf-8", errors="replace").splitlines()) if outbox.exists() else 0
    session_win = find_cloudpc_session_window(use_cua=False)
    return {
        "todesk": asdict(telem),
        "session_window": session_win,
        "a2a": {
            "dir": str(base),
            "inbox_exists": inbox.exists(),
            "inbox_count": inbox_lines,
            "outbox_exists": outbox.exists(),
            "outbox_count": outbox_lines,
        },
    }


def capture_cloudpc_offscreen(out_path: str | Path) -> Dict[str, Any]:
    """Capture the ToDesk Cloud PC remote window (or main window) offscreen."""
    win = find_cloudpc_session_window(use_cua=False)
    target_hwnd = win["window_id"] if win else None
    if not target_hwnd:
        telem = get_todesk_telemetry()
        target_hwnd = telem.remote_window_id or telem.main_window_id
    if not target_hwnd:
        return {"ok": False, "error": "no ToDesk window found"}
    with UserInputGuard() as guard:
        cap: CaptureResult = capture_window_offscreen(target_hwnd, out_path)
    return {
        **asdict(cap),
        "captured_window_role": "remote_desktop" if (win or (target_hwnd and target_hwnd != (get_todesk_telemetry().main_window_id))) else "main_panel",
        "guard": guard.summary(),
    }


def peek_cloudpc_screen(
    out_path: str | Path,
    *,
    mode: str = "auto",
    driver_path: Optional[str] = None,
    bring_to_front: bool = False,
) -> Dict[str, Any]:
    """Capture Cloud PC screen with fallback between PrintWindow and cua-driver.

    Modes:
      - 'auto': Attempts offscreen PrintWindow first; if failed/empty falls back to cua-driver
      - 'offscreen': Only PrintWindow (pure non-intrusive)
      - 'cua': Uses cua-driver get_desktop_state
    """
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    win = find_cloudpc_session_window(driver_path=driver_path)

    # 1. Offscreen PrintWindow path
    if mode in ("auto", "offscreen"):
        target_hwnd = (win and win["window_id"]) or None
        if not target_hwnd:
            telem = get_todesk_telemetry()
            target_hwnd = telem.remote_window_id or telem.main_window_id
        if target_hwnd:
            with UserInputGuard() as guard:
                cap = capture_window_offscreen(target_hwnd, out_file)
            if cap.ok and out_file.exists() and out_file.stat().st_size > 1000:
                return {
                    "ok": True,
                    "method": "offscreen",
                    "image": str(out_file),
                    "bytes": cap.size_bytes,
                    "session_window": win,
                    "guard": guard.summary(),
                }
            if mode == "offscreen":
                return {
                    "ok": False,
                    "method": "offscreen",
                    "error": cap.error or "offscreen capture produced empty or invalid image",
                    "session_window": win,
                }

    # 2. Cua Driver path (cloudpc_peek fallback)
    if mode in ("auto", "cua"):
        front_res = None
        driver = resolve_cua_driver_path(driver_path)
        if bring_to_front and win and win.get("window_id"):
            front_res = call_cua_driver_tool(
                "bring_to_front",
                {"pid": win["pid"], "window_id": win["window_id"]},
                executable=driver,
            )
            time.sleep(1.0)

        shot = call_cua_driver_tool(
            "get_desktop_state",
            {"screenshot_out_file": str(out_file)},
            executable=driver,
        )
        ok = bool(shot and out_file.exists() and out_file.stat().st_size > 0)
        return {
            "ok": ok,
            "method": "cua-driver",
            "image": str(out_file) if ok else None,
            "bytes": out_file.stat().st_size if ok else 0,
            "session_window": win,
            "raised": bool(front_res and front_res.get("landed_on_target")),
            "screen": {
                k: shot.get(k)
                for k in ("screen_width", "screen_height", "scale_factor")
            }
            if isinstance(shot, dict)
            else None,
        }

    return {"ok": False, "error": f"unsupported capture mode {mode!r}"}


def enqueue_cloudpc_a2a(
    task_id: str,
    cmd: str,
    *,
    a2a_dir: str | Path = DEFAULT_A2A_DIR,
    timeout_s: int = 120,
    note: str = "",
) -> Dict[str, Any]:
    """Append a bounded command task to the Cloud PC A2A inbox."""
    base = Path(a2a_dir)
    base.mkdir(parents=True, exist_ok=True)
    rec = {
        "task_id": task_id,
        "ts": datetime.now(CST).isoformat(timespec="seconds"),
        "mode": "shell",
        "cmd": cmd,
        "timeout_s": int(timeout_s),
        "note": note,
    }
    inbox = base / "inbox.jsonl"
    with inbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "inbox": str(inbox), "record": rec}


def route_cloudpc_window_action(
    kind: str,
    parameters: Dict[str, Any],
    *,
    agent_id: str = "acf-cloudpc",
    backend_mode: str = "composite",
    driver_path: Optional[str] = None,
    allow_foreground: bool = False,
    target_window: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Route a background window-local input action to the ToDesk remote desktop window.

    Supports composite (Win32 -> cua-driver fallback), pure cua-driver, Win32, or dry-run.
    """
    win = target_window or find_cloudpc_session_window(driver_path=driver_path)
    if not win or not win.get("window_id") or not win.get("pid"):
        return {
            "ok": False,
            "status": "blocked",
            "reason": "ToDesk remote session window is not open or not discoverable",
        }

    target = Target(kind="window", pid=int(win["pid"]), window_id=int(win["window_id"]))
    drv = resolve_cua_driver_path(driver_path)

    backend: Backend
    if backend_mode == "dry-run":
        backend = DryRunBackend()
    elif backend_mode == "win32":
        backend = Win32Backend(allow_foreground=allow_foreground)
    elif backend_mode == "cua-driver":
        backend = CuaDriverBackend(executable=drv, allow_foreground=allow_foreground)
    else:  # default composite
        win32_b = Win32Backend(allow_foreground=allow_foreground)
        cua_b = CuaDriverBackend(executable=drv, allow_foreground=allow_foreground)
        backend = CompositeBackend([win32_b, cua_b])

    router = ActionRouter(backend, use_cross_process_locks=True)
    delivery = "foreground" if allow_foreground else "background"
    act = Action(
        action_id=f"cloudpc-{kind}-{int(time.time()*1000)}",
        agent_id=agent_id,
        source="acf-cloudpc",
        kind=kind,
        target=target,
        parameters=parameters,
        delivery_mode=delivery,
    )
    with UserInputGuard() as guard:
        res = router.execute(act)

    return {
        "ok": res.ok,
        "status": res.status,
        "backend": res.backend,
        "details": res.details,
        "target": target.to_dict(),
        "session_window": win,
        "guard": guard.summary(),
    }
