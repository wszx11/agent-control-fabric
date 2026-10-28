"""Normalize planner-specific action formats into the canonical protocol."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

from .models import Action, ActionValidationError, Target


_ALIAS = {
    "left_click": "click",
    "left_single": "click",
    "left_double": "double_click",
    "doubleclick": "double_click",
    "right_single": "right_click",
    "middle_single": "middle_click",
    "input": "type",
    "text": "type",
    "key": "press",
    "keyboard": "press",
    "key_press": "press",
    "scroll_page": "scroll",
    "sleep": "wait",
}


def _literal(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return value


def _parse_call(value: str) -> tuple[str, dict[str, Any]]:
    text = value.strip()
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError as exc:
        raise ActionValidationError(f"invalid serialized planner action: {value}") from exc
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise ActionValidationError("serialized action must be a function call")
    if node.args:
        raise ActionValidationError("positional planner action arguments are unsupported")
    params: dict[str, Any] = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            raise ActionValidationError("**kwargs are unsupported in planner actions")
        params[keyword.arg] = ast.literal_eval(keyword.value)
    return node.func.id, params


def _unwrap(raw: Any) -> tuple[str | None, dict[str, Any]]:
    if isinstance(raw, str):
        name, params = _parse_call(raw)
        return name, params
    if not isinstance(raw, Mapping):
        raise ActionValidationError("planner action must be a mapping or call string")

    candidate = raw
    nested = raw.get("action")
    if isinstance(nested, str):
        name, params = _parse_call(nested) if "(" in nested else (nested, {})
        merged = dict(raw)
        merged.pop("action", None)
        merged.pop("type", None)
        merged.pop("name", None)
        merged.update(params)
        for envelope_key in ("parameters", "params", "args", "arguments", "inputs"):
            nested_params = merged.pop(envelope_key, None)
            if isinstance(nested_params, Mapping):
                merged.update(nested_params)
                break
        return name, merged
    if isinstance(nested, Mapping):
        candidate = nested

    name = (
        candidate.get("kind")
        or candidate.get("type")
        or candidate.get("name")
        or candidate.get("action_type")
        or candidate.get("actionType")
        or candidate.get("command")
    )
    params: dict[str, Any] = dict(candidate)
    for envelope_key in ("kind", "type", "name", "action_type", "actionType", "command"):
        params.pop(envelope_key, None)
    for envelope_key in ("parameters", "params", "args", "arguments", "inputs"):
        nested_params = params.pop(envelope_key, None)
        if isinstance(nested_params, Mapping):
            merged = dict(params)
            merged.update(nested_params)
            params = merged
            break
    return (str(name) if name is not None else None), params


def _first(params: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in params and params[key] is not None:
            return params[key]
    return default


def _point(value: Any) -> tuple[float, float] | None:
    value = _literal(value)
    if isinstance(value, Mapping):
        for key in ("raw", "point", "center", "normalized"):
            if key in value:
                point = _point(value[key])
                if point:
                    return point
        if "x" in value and "y" in value:
            return float(value["x"]), float(value["y"])
        return None
    if isinstance(value, (list, tuple)):
        if len(value) == 2:
            return float(value[0]), float(value[1])
        if len(value) == 4:
            return (float(value[0]) + float(value[2])) / 2, (
                float(value[1]) + float(value[3])
            ) / 2
    return None


def _text(value: Any) -> str:
    if value is None:
        raise ActionValidationError("type action requires content")
    return str(value)


def normalize_action(
    source: str,
    raw: Any,
    *,
    target: Target | Mapping[str, Any],
    action_id: str | None = None,
    agent_id: str = "agent-unknown",
    delivery_mode: str = "background",
    expected: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    coord_space: str = "pixel",
    window_size: tuple[int, int] | None = None,
) -> Action:
    """Normalize one coordinate/DSL/semantic or generic planner action."""

    name, params = _unwrap(raw)
    if not name:
        raise ActionValidationError("planner action has no action kind")
    kind = _ALIAS.get(str(name).strip().lower(), str(name).strip().lower())
    target_obj = Target.from_mapping(target)
    action_id = action_id or str(params.pop("action_id", "action-unnamed"))
    agent_id = str(params.pop("agent_id", agent_id))
    delivery_mode = str(params.pop("delivery_mode", delivery_mode))
    raw_expected = params.pop("expected", expected)
    space = str(params.pop("coord_space", coord_space)).strip().lower()
    win_size = params.pop("window_size", window_size)

    def _scale_pt(pt: tuple[float, float] | None) -> tuple[float, float] | None:
        if pt is None:
            return None
        if space == "normalized_1000" and win_size is not None and len(win_size) == 2:
            w, h = float(win_size[0]), float(win_size[1])
            return (round(pt[0] * w / 1000.0, 2), round(pt[1] * h / 1000.0, 2))
        return pt

    if kind in {"click", "double_click", "right_click", "middle_click"}:
        point = _scale_pt(_point(_first(params, "point", "start", "start_point", "start_box", "box")))
        canonical: dict[str, Any] = {}
        if point:
            canonical.update(x=point[0], y=point[1])
        for key in ("element_token", "element_index", "snapshot_id", "modifier", "selector"):
            if key in params:
                canonical[key] = params[key]
        if not canonical:
            raise ActionValidationError(f"{kind} requires point, box, selector, or element token")
    elif kind == "type":
        canonical = {"content": _text(_first(params, "content", "input_text", "text", "value"))}
        for key in ("element_token", "element_index", "snapshot_id", "selector"):
            if key in params:
                canonical[key] = params[key]
        if "delay_ms" in params:
            canonical["delay_ms"] = params["delay_ms"]
    elif kind == "press":
        key = _first(params, "key", "keyboard_key", "input_key")
        if key is None:
            raise ActionValidationError("press action requires key")
        canonical = {"key": str(key)}
        for field in ("element_token", "element_index", "snapshot_id"):
            if field in params:
                canonical[field] = params[field]
    elif kind == "hotkey":
        keys = _first(params, "keys", "key", "keyboard_keys")
        if isinstance(keys, str):
            keys = [part for part in re.split(r"[+\s,]+", keys) if part]
        if not isinstance(keys, (list, tuple)) or len(keys) < 2:
            raise ActionValidationError("hotkey requires at least two keys")
        canonical = {"keys": [str(key) for key in keys]}
    elif kind == "scroll":
        direction = str(_first(params, "direction", "dir", default="down")).lower()
        if direction not in {"up", "down", "left", "right"}:
            raise ActionValidationError(f"unsupported scroll direction {direction!r}")
        canonical = {
            "direction": direction,
            "amount": int(_first(params, "amount", "quantity", default=1)),
            "by": str(_first(params, "by", "unit", default="line")),
        }
    elif kind == "drag":
        start = _point(_first(params, "start", "from", "start_point", "from_point"))
        end = _point(_first(params, "end", "to", "end_point", "to_point"))
        if not start or not end:
            raise ActionValidationError("drag requires start and end points")
        canonical = {
            "from_x": start[0],
            "from_y": start[1],
            "to_x": end[0],
            "to_y": end[1],
        }
        for field in ("button", "duration_ms", "steps", "modifier"):
            if field in params:
                canonical[field] = params[field]
    elif kind == "wait":
        value = _first(params, "time", "wait_time", "duration", default=0)
        canonical = {"time": float(str(value).rstrip("s"))}
    else:
        raise ActionValidationError(f"unsupported planner action kind {kind!r}")

    return Action(
        action_id=str(action_id),
        agent_id=agent_id,
        source=source,
        kind=kind,
        target=target_obj,
        parameters=canonical,
        delivery_mode=delivery_mode,
        expected=tuple(raw_expected or ()),
    )


class CoordinateAdapter:
    source = "coordinate"

    def normalize(self, raw: Any, **kwargs: Any) -> Action:
        return normalize_action(self.source, raw, **kwargs)


class DslAdapter:
    source = "dsl"

    def normalize(self, raw: Any, **kwargs: Any) -> Action:
        return normalize_action(self.source, raw, **kwargs)


class SemanticAdapter:
    source = "semantic"

    def normalize(self, raw: Any, **kwargs: Any) -> Action:
        return normalize_action(self.source, raw, **kwargs)


ADAPTERS = {
    "semantic": SemanticAdapter(),
    "coordinate": CoordinateAdapter(),
    "dsl": DslAdapter(),
}


# Short aliases
CoordinateAdapterAlias = CoordinateAdapter
DslAdapterAlias = DslAdapter
SemanticAdapterAlias = SemanticAdapter
