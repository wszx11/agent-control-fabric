"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .backends import CuaDriverBackend, DryRunBackend
from .models import Action, Target
from .normalize import normalize_action
from .router import ActionRouter


def _load_actions(path: str) -> list[Action]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("actions", [value])
    if not isinstance(value, list):
        raise ValueError("action file must contain an object or array")
    return [Action.from_mapping(item) for item in value]


def _result_json(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "action_id": result.action_id,
        "backend": result.backend,
        "details": result.details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acf")
    sub = parser.add_subparsers(dest="command", required=True)

    normalize_parser = sub.add_parser("normalize")
    normalize_parser.add_argument("--source", required=True)
    normalize_parser.add_argument("--action", required=True)
    normalize_parser.add_argument("--pid", type=int, required=True)
    normalize_parser.add_argument("--window-id", type=int, required=True)
    normalize_parser.add_argument("--agent-id", default="agent-cli")
    normalize_parser.add_argument("--action-id", default="action-cli")
    normalize_parser.add_argument("--delivery-mode", default="background")

    run_parser = sub.add_parser("run")
    run_parser.add_argument("path")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--driver", default="cua-driver")
    run_parser.add_argument("--allow-foreground", action="store_true")
    run_parser.add_argument("--max-workers", type=int, default=4)

    args = parser.parse_args(argv)
    try:
        if args.command == "normalize":
            action = normalize_action(
                args.source,
                args.action,
                target=Target(kind="window", pid=args.pid, window_id=args.window_id),
                action_id=args.action_id,
                agent_id=args.agent_id,
                delivery_mode=args.delivery_mode,
            )
            print(json.dumps(action.to_dict(), ensure_ascii=False, indent=2))
            return 0

        actions = _load_actions(args.path)
        backend = (
            DryRunBackend()
            if args.dry_run
            else CuaDriverBackend(
                executable=args.driver,
                allow_foreground=args.allow_foreground,
            )
        )
        results = ActionRouter(backend, max_workers=args.max_workers).execute_many(actions)
        print(json.dumps([_result_json(result) for result in results], ensure_ascii=False, indent=2))
        return 0 if all(result.status in {"executed", "verified"} for result in results) else 2
    except Exception as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
