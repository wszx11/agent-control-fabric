"""Command-line entry point for agent-control-fabric (v0.2.0)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .backends import CompositeBackend, CuaDriverBackend, DryRunBackend
from .locks import active_keys, peek
from .models import Action, Target
from .normalize import normalize_action
from .router import ActionRouter
from .todesk import copy_files_to_clipboard, get_todesk_telemetry, prepare_sanitized_rt_bundle
from .win32_backend import Win32Backend


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
    parser = argparse.ArgumentParser(prog="acf", description="Agent Control Fabric CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # normalize
    normalize_parser = sub.add_parser("normalize", help="Normalize third-party actions to canonical format")
    normalize_parser.add_argument("--source", required=True)
    normalize_parser.add_argument("--action", required=True)
    normalize_parser.add_argument("--pid", type=int, required=True)
    normalize_parser.add_argument("--window-id", type=int, required=True)
    normalize_parser.add_argument("--agent-id", default="agent-cli")
    normalize_parser.add_argument("--action-id", default="action-cli")
    normalize_parser.add_argument("--delivery-mode", default="background")

    # run
    run_parser = sub.add_parser("run", help="Execute canonical actions against a backend")
    run_parser.add_argument("path")
    run_parser.add_argument("--backend", choices=["win32", "cua-driver", "composite", "dry-run"], default="composite")
    run_parser.add_argument("--dry-run", action="store_true", help="Alias for --backend dry-run")
    run_parser.add_argument("--driver", default="cua-driver", help="Cua Driver binary path")
    run_parser.add_argument("--allow-foreground", action="store_true", help="Allow foreground focus actions if explicitly required")
    run_parser.add_argument("--cross-process-locks", action="store_true", help="Enforce cross-process file-level mutual exclusion locks")
    run_parser.add_argument("--max-workers", type=int, default=4)

    # locks
    locks_parser = sub.add_parser("locks", help="Inspect cross-process resource locks")
    locks_sub = locks_parser.add_subparsers(dest="locks_cmd", required=True)
    locks_sub.add_parser("list", help="List active resource lock keys")
    peek_parser = locks_sub.add_parser("peek", help="Inspect a specific lock key")
    peek_parser.add_argument("key", help="Resource lock key")

    # todesk
    todesk_parser = sub.add_parser("todesk", help="ToDesk remote desktop inspection and staging")
    todesk_sub = todesk_parser.add_subparsers(dest="todesk_cmd", required=True)
    todesk_sub.add_parser("status", help="Get live ToDesk window and connection telemetry")
    stage_parser = todesk_sub.add_parser("stage-rt", help="Stage sanitized RT bundle stripping plaintext passwords")
    stage_parser.add_argument("--source", required=True, help="Directory containing .tar.enc and .sha256.txt")
    stage_parser.add_argument("--output", required=True, help="Output sanitized .zip path")
    clip_parser = todesk_sub.add_parser("clipboard", help="Copy files into Windows CF_HDROP clipboard")
    clip_parser.add_argument("files", nargs="+", help="File paths to place on clipboard")

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

        if args.command == "locks":
            if args.locks_cmd == "list":
                keys = active_keys()
                print(json.dumps({"active_lock_keys": keys, "count": len(keys)}, ensure_ascii=False, indent=2))
                return 0
            if args.locks_cmd == "peek":
                rec = peek(args.key)
                print(json.dumps(rec or {"status": "unlocked", "key": args.key}, ensure_ascii=False, indent=2))
                return 0

        if args.command == "todesk":
            if args.todesk_cmd == "status":
                telem = get_todesk_telemetry()
                print(json.dumps(asdict(telem), ensure_ascii=False, indent=2))
                return 0
            if args.todesk_cmd == "stage-rt":
                res = prepare_sanitized_rt_bundle(args.source, args.output)
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0
            if args.todesk_cmd == "clipboard":
                ok = copy_files_to_clipboard(args.files)
                print(json.dumps({"clipboard_cf_hdrop_set": ok, "files": args.files}, ensure_ascii=False, indent=2))
                return 0 if ok else 1

        if args.command == "run":
            actions = _load_actions(args.path)
            backend_choice = "dry-run" if args.dry_run else args.backend

            if backend_choice == "dry-run":
                backend = DryRunBackend()
            elif backend_choice == "win32":
                backend = Win32Backend(allow_foreground=args.allow_foreground)
            elif backend_choice == "cua-driver":
                backend = CuaDriverBackend(executable=args.driver, allow_foreground=args.allow_foreground)
            else:  # composite
                win32_b = Win32Backend(allow_foreground=args.allow_foreground)
                cua_b = CuaDriverBackend(executable=args.driver, allow_foreground=args.allow_foreground)
                backend = CompositeBackend([win32_b, cua_b])

            router = ActionRouter(
                backend,
                max_workers=args.max_workers,
                use_cross_process_locks=args.cross_process_locks,
            )
            results = router.execute_many(actions)
            print(json.dumps([_result_json(result) for result in results], ensure_ascii=False, indent=2))
            return 0 if all(result.status in {"executed", "verified"} for result in results) else 2

        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
