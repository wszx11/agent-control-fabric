"""Canonical data types for Agent Control Fabric."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


WINDOW_ACTIONS = {
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "type",
    "press",
    "hotkey",
    "scroll",
    "drag",
    "wait",
}
ALLOWED_TARGETS = {"window", "browser"}
DELIVERY_MODES = {"background", "foreground"}
STATUSES = {"executed", "verified", "blocked", "failed", "unknown"}


class ActionValidationError(ValueError):
    """Raised when an action would violate the canonical safety contract."""


@dataclass(frozen=True)
class Target:
    """A logical execution target; window targets are always exact."""

    kind: str
    pid: int | None = None
    window_id: int | None = None
    session_id: str | None = None
    tab_id: str | None = None

    def __post_init__(self) -> None:
        kind = str(self.kind).strip().lower()
        object.__setattr__(self, "kind", kind)
        if kind not in ALLOWED_TARGETS:
            raise ActionValidationError(
                f"unsupported target kind {self.kind!r}; use window or browser"
            )
        if kind == "window":
            if not isinstance(self.pid, int) or self.pid <= 0:
                raise ActionValidationError("window target requires a positive pid")
            if not isinstance(self.window_id, int) or self.window_id <= 0:
                raise ActionValidationError(
                    "window target requires a positive window_id"
                )
        if kind == "browser" and not (self.session_id or self.tab_id):
            raise ActionValidationError(
                "browser target requires session_id or tab_id"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "Target") -> "Target":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ActionValidationError("target must be an object")
        return cls(
            kind=str(value.get("kind", "")),
            pid=value.get("pid"),
            window_id=value.get("window_id"),
            session_id=value.get("session_id") or value.get("session"),
            tab_id=value.get("tab_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        for key in ("pid", "window_id", "session_id", "tab_id"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result

    def lock_key(self) -> tuple[Any, ...]:
        """Return the physical target lock key.

        Window locks intentionally omit session_id: two logical agents must not
        interleave input into one native window. Browser locks include the tab
        identity when available so separate tabs can run concurrently.
        """

        if self.kind == "window":
            return (self.kind, self.pid, self.window_id)
        return (self.kind, self.session_id, self.tab_id)


@dataclass(frozen=True)
class Action:
    """Normalized action emitted by every planner adapter."""

    action_id: str
    agent_id: str
    source: str
    kind: str
    target: Target
    parameters: Mapping[str, Any] = field(default_factory=dict)
    delivery_mode: str = "background"
    expected: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        action_id = str(self.action_id).strip()
        agent_id = str(self.agent_id).strip()
        source = str(self.source).strip().lower()
        kind = str(self.kind).strip().lower()
        delivery = str(self.delivery_mode).strip().lower()
        if not action_id:
            raise ActionValidationError("action_id is required")
        if not agent_id:
            raise ActionValidationError("agent_id is required")
        if kind not in WINDOW_ACTIONS:
            raise ActionValidationError(f"unsupported action kind {kind!r}")
        if delivery not in DELIVERY_MODES:
            raise ActionValidationError(f"unsupported delivery mode {delivery!r}")
        if not isinstance(self.target, Target):
            raise ActionValidationError("target must be a Target")
        if self.target.kind == "browser" and kind not in {
            "click",
            "double_click",
            "right_click",
            "middle_click",
            "type",
            "press",
            "hotkey",
            "scroll",
            "drag",
            "wait",
        }:
            raise ActionValidationError("unsupported browser action")
        if not isinstance(self.parameters, Mapping):
            raise ActionValidationError("parameters must be an object")
        if not isinstance(self.expected, tuple):
            object.__setattr__(self, "expected", tuple(self.expected or ()))
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "source", source or "generic")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "delivery_mode", delivery)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Action":
        if not isinstance(value, Mapping):
            raise ActionValidationError("action must be an object")
        return cls(
            action_id=str(value.get("action_id", "")),
            agent_id=str(value.get("agent_id", "")),
            source=str(value.get("source", "generic")),
            kind=str(value.get("kind", "")),
            target=Target.from_mapping(value.get("target", {})),
            parameters=dict(value.get("parameters") or {}),
            delivery_mode=str(value.get("delivery_mode", "background")),
            expected=tuple(value.get("expected") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "agent_id": self.agent_id,
            "source": self.source,
            "kind": self.kind,
            "target": self.target.to_dict(),
            "parameters": dict(self.parameters),
            "delivery_mode": self.delivery_mode,
            "expected": [dict(item) for item in self.expected],
        }


@dataclass
class BackendResult:
    """Result returned by an execution backend."""

    status: str
    action_id: str
    backend: str
    details: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid backend result status {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status in {"executed", "verified"}
