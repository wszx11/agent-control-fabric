"""Dual-track Cloud PC controller (A2A command channel + ToDesk offscreen GUI).

Manages the remote Cloud PC (e.g. a ToDesk remote host)
without ever blocking or interrupting the local user's mouse or keyboard:
- Track A (Preferred): Enqueues commands into `cloudpc_a2a/inbox.jsonl` or
  delivers sanitized bundles via ToDesk File Transfer in the background.
- Track B (Fallback GUI): Captures the Cloud PC screen offscreen via
  `PrintWindow` (works even when ToDesk is occluded behind local windows!)
  and sends window-local background mouse/keyboard messages through ActionRouter.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .capture import CaptureResult, capture_window_offscreen
from .layout import UserInputGuard
from .models import Action, Target
from .router import ActionRouter
from .todesk import get_todesk_telemetry
from .win32_backend import Win32Backend

CST = timezone(timedelta(hours=8))
DEFAULT_A2A_DIR = Path(os.environ.get("ACF_A2A_DIR", str(Path.home() / ".acf" / "cloudpc_a2a")))


def cloudpc_status(a2a_dir: str | Path = DEFAULT_A2A_DIR) -> Dict[str, Any]:
    """Return combined status of ToDesk remote window and Cloud PC A2A queues."""
    telem = get_todesk_telemetry()
    base = Path(a2a_dir)
    inbox = base / "inbox.jsonl"
    outbox = base / "outbox.jsonl"
    inbox_lines = len(inbox.read_text(encoding="utf-8", errors="replace").splitlines()) if inbox.exists() else 0
    outbox_lines = len(outbox.read_text(encoding="utf-8", errors="replace").splitlines()) if outbox.exists() else 0
    return {
        "todesk": asdict(telem),
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
    telem = get_todesk_telemetry()
    target_hwnd = telem.remote_window_id or telem.main_window_id
    if not target_hwnd:
        return {"ok": False, "error": "no ToDesk window found"}
    with UserInputGuard() as guard:
        cap: CaptureResult = capture_window_offscreen(target_hwnd, out_path)
    return {
        **asdict(cap),
        "captured_window_role": "remote_desktop" if telem.remote_window_id else "main_panel",
        "guard": guard.summary(),
    }


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
) -> Dict[str, Any]:
    """Route a background window-local input action to the ToDesk remote desktop window."""
    telem = get_todesk_telemetry()
    if not telem.remote_window_id or not telem.pids:
        return {"ok": False, "status": "blocked", "reason": "ToDesk remote session window is not open"}
    router = ActionRouter(Win32Backend(), use_cross_process_locks=True)
    act = Action(
        action_id=f"cloudpc-{kind}-{int(time.time()*1000)}",
        agent_id=agent_id,
        source="acf-cloudpc",
        kind=kind,
        target=Target(kind="window", pid=telem.pids[-1], window_id=telem.remote_window_id),
        parameters=parameters,
        delivery_mode="background",
    )
    with UserInputGuard() as guard:
        res = router.execute(act)
    return {
        "ok": res.ok,
        "status": res.status,
        "backend": res.backend,
        "details": res.details,
        "guard": guard.summary(),
    }
