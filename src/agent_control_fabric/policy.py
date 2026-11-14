"""Focus-safety and no-foreground policy helpers for Agent Control Fabric."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Optional, Tuple

from .models import Action

NO_FOREGROUND_ENV = "ACF_NO_FOREGROUND"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _parse_bool_value(value: Any) -> Optional[bool]:
    """Strictly parse bool/int/str flag values; return None when unrecognized."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        norm = value.strip().lower()
        if not norm:
            return None
        if norm in _TRUTHY:
            return True
        if norm in _FALSY:
            return False
    return None


def parse_bool_env(name: str) -> Optional[bool]:
    """Parse a boolean environment variable, or return None when unset/unrecognized."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    return _parse_bool_value(raw)


def is_no_foreground_enforced() -> Optional[bool]:
    """Return True if ACF_NO_FOREGROUND forces no-foreground, False if explicitly off, else None."""
    return parse_bool_env(NO_FOREGROUND_ENV)


def is_foreground_request(action: Action) -> Tuple[bool, str]:
    """Return (True, reason) when *action* asks to raise or focus a window in the foreground."""
    delivery_mode = str(getattr(action, "delivery_mode", "") or "").strip().lower()
    if delivery_mode == "foreground":
        return True, "delivery_mode='foreground' is disallowed in no-foreground mode"
    raw_params = getattr(action, "parameters", None)
    params: Mapping[str, Any] = raw_params if isinstance(raw_params, Mapping) else {}
    if _parse_bool_value(params.get("bring_to_front")) is True:
        return True, "parameter 'bring_to_front' attempts to pull window to foreground"
    if _parse_bool_value(params.get("raise_window")) is True:
        return True, "parameter 'raise_window' attempts to pull window to foreground"
    kind = str(getattr(action, "kind", "") or "").strip().lower()
    if kind == "focus_app" and _parse_bool_value(params.get("raise_window")) is not False:
        return True, "action 'focus_app' without raise_window=False may steal foreground focus"
    return False, ""


def check_foreground_allowed(
    action: Action,
    *,
    allow_foreground: bool = False,
    no_foreground: Optional[bool] = None,
) -> Tuple[bool, str]:
    """Check whether *action* may execute under foreground/no-foreground constraints."""
    is_fg, reason = is_foreground_request(action)
    if not is_fg:
        return True, ""
    if is_no_foreground_enforced() is True:
        return False, f"blocked by {NO_FOREGROUND_ENV}: {reason}"
    if no_foreground is True:
        return False, f"blocked by no_foreground policy: {reason}"
    if not allow_foreground:
        return False, f"foreground execution blocked by default: {reason}"
    return True, ""
