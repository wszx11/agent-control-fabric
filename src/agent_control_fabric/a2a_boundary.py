# -*- coding: utf-8 -*-
"""Deny-by-default boundary for A2A mailbox operations.

The cloud-side watcher only needs to answer read-only questions about fixed
paths. Everything else must be refused, not silently ignored: a mailbox that
accepts arbitrary verb names is a remote code execution surface.

Observed in the wild (2026-10-29): a task asked for ``op=run_script`` and the
watcher answered ``{"ok": false, "error": "unknown_op"}``. That refusal was
correct. This module makes the same rule explicit and shared, so the boundary
does not depend on each watcher remembering to keep its allow-list short.
"""
from __future__ import annotations

from typing import Any

#: The only operations a mailbox consumer may perform: fixed-path, read-only reads.
ALLOWED_OPS: frozenset[str] = frozenset({"branch_status_probe"})

#: Documented never-ops. Kept for explicit refusal messages and for tests; any
#: operation not in ALLOWED_OPS is refused anyway (deny-by-default).
FORBIDDEN_OPS: frozenset[str] = frozenset({
    "run_script", "run_command", "run_powershell", "cmd", "command", "shell",
    "exec", "write_file", "delete_file", "reg_write", "service_control",
    "install", "download", "upload", "screenshot", "clipboard_read",
})

#: Human-readable reason used in refusal payloads.
REFUSAL_CODE = "unknown_op"


def normalize_op(op: Any) -> str:
    """Lower-case and trim an operation name; non-string input becomes empty."""
    if isinstance(op, bytes):
        try:
            op = op.decode("utf-8", "replace")
        except Exception:
            return ""
    if not isinstance(op, str):
        return ""
    return op.strip().lower()


def is_allowed(op: Any) -> bool:
    """True only for an exact, allow-listed operation."""
    return normalize_op(op) in ALLOWED_OPS


def refusal(op: Any) -> dict[str, Any]:
    """Build the refusal payload for an operation that must not run."""
    return {"ok": False, "error": REFUSAL_CODE, "op": normalize_op(op)}


def describe() -> dict[str, Any]:
    """Machine-readable summary of the boundary (handy for docs and CLI)."""
    return {
        "policy": "deny-by-default",
        "allowed_ops": sorted(ALLOWED_OPS),
        "forbidden_ops": sorted(FORBIDDEN_OPS),
        "refusal_code": REFUSAL_CODE,
    }
