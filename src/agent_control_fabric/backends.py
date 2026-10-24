"""Execution backends and the Cua Driver process boundary."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from typing import Any, Protocol

from .models import Action, BackendResult


class Backend(Protocol):
    name: str

    def execute(self, action: Action) -> BackendResult:
        ...


class DryRunBackend:
    name = "dry-run"

    def execute(self, action: Action) -> BackendResult:
        return BackendResult(
            status="executed",
            action_id=action.action_id,
            backend=self.name,
            details={"dry_run": True, "action": action.to_dict()},
        )


class FunctionBackend:
    """Small adapter for CDP, OpenAdapt, or another external service."""

    def __init__(self, name: str, function: Callable[[Action], Any]):
        self.name = name
        self._function = function

    def execute(self, action: Action) -> BackendResult:
        try:
            value = self._function(action)
        except Exception as exc:  # backend exceptions become auditable results
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"error": str(exc), "error_type": type(exc).__name__},
            )
        if isinstance(value, BackendResult):
            return value
        return BackendResult(
            status="executed",
            action_id=action.action_id,
            backend=self.name,
            details={"result": value},
        )


class CuaDriverBackend:
    """Call the installed Cua Driver without allowing implicit foreground input."""

    name = "cua-driver"
    _TOOLS = {
        "click": "click",
        "double_click": "double_click",
        "right_click": "right_click",
        "middle_click": "middle_click",
        "type": "type_text",
        "press": "press_key",
        "hotkey": "hotkey",
        "scroll": "scroll",
        "drag": "drag",
    }

    def __init__(
        self,
        executable: str = "cua-driver",
        *,
        timeout: float = 30.0,
        allow_foreground: bool = False,
    ) -> None:
        self.executable = executable
        self.timeout = timeout
        self.allow_foreground = allow_foreground

    def _payload(self, action: Action) -> dict[str, Any]:
        if action.target.kind != "window":
            raise ValueError("Cua Driver backend requires a window target")
        payload: dict[str, Any] = {
            "pid": action.target.pid,
            "window_id": action.target.window_id,
            "session": action.target.session_id or action.agent_id,
            "delivery_mode": action.delivery_mode,
        }
        payload.update(dict(action.parameters))
        return payload

    def _invoke(self, tool: str, payload: dict[str, Any]) -> tuple[int, str, str]:
        completed = subprocess.run(
            [self.executable, "call", tool, json.dumps(payload, ensure_ascii=True)],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    @staticmethod
    def _decode(stdout: str) -> Any:
        text = stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def execute(self, action: Action) -> BackendResult:
        if action.target.kind != "window":
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "target_not_supported", "message": "Cua requires a window target"},
            )
        if action.delivery_mode == "foreground" and not self.allow_foreground:
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={
                    "code": "foreground_not_allowed",
                    "message": "foreground execution requires explicit backend configuration",
                },
            )
        if action.kind == "wait":
            time.sleep(max(0.0, float(action.parameters.get("time", 0))))
            return BackendResult(
                status="executed",
                action_id=action.action_id,
                backend=self.name,
                details={"tool": "local_wait"},
            )
        tool = self._TOOLS[action.kind]
        payload = self._payload(action)
        try:
            returncode, stdout, stderr = self._invoke(tool, payload)
        except FileNotFoundError as exc:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "driver_not_found", "message": str(exc)},
            )
        except subprocess.TimeoutExpired:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "driver_timeout", "timeout_seconds": self.timeout},
            )
        decoded = self._decode(stdout)
        text = f"{stdout}\n{stderr}".lower()
        if "background_unavailable" in text:
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={
                    "code": "background_unavailable",
                    "message": "driver refused background delivery; no foreground retry was attempted",
                    "driver": decoded,
                },
                raw=decoded,
            )
        if returncode != 0:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "driver_error", "returncode": returncode, "driver": decoded},
                raw=decoded,
            )
        return BackendResult(
            status="executed",
            action_id=action.action_id,
            backend=self.name,
            details={"tool": tool, "driver": decoded},
            raw=decoded,
        )
