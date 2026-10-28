"""Command-line entry point for agent-control-fabric (v0.5.1)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .backends import CompositeBackend, CuaDriverBackend, DryRunBackend
from .capture import capture_window_offscreen
from .cloudpc import capture_cloudpc_offscreen, cloudpc_status, enqueue_cloudpc_a2a
from .layout import PARKING_SLOTS, park_window_noactivate
from .locks import active_keys, peek
from .models import Action, Target
from .normalize import normalize_action
from .retrospect import run_retrospect
from .router import ActionRouter
from .todesk import copy_files_to_clipboard, get_todesk_telemetry, prepare_sanitized_bundle
from .uia_backend import UIABackend, observe_window_uia
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
    parser = argparse.ArgumentParser(prog="acf", description="Agent Control Fabric CLI (v0.5.1)")
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
    normalize_parser.add_argument("--coord-space", choices=["pixel", "normalized_1000"], default="pixel")
    normalize_parser.add_argument("--window-width", type=int, default=None)
    normalize_parser.add_argument("--window-height", type=int, default=None)

    # run
    run_parser = sub.add_parser("run", help="Execute canonical actions against a backend")
    run_parser.add_argument("path")
    run_parser.add_argument(
        "--backend",
        choices=["uia", "win32", "cua-driver", "composite", "dry-run"],
        default="composite",
    )
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

    # pc (local desktop non-intrusive control)
    pc_parser = sub.add_parser("pc", help="Focus-safe local PC window management")
    pc_sub = pc_parser.add_subparsers(dest="pc_cmd", required=True)
    lw_p = pc_sub.add_parser("list-windows", help="List visible top-level windows")
    lw_p.add_argument("--filter", default="", help="Title substring filter")
    cap_p = pc_sub.add_parser("capture", help="Offscreen PrintWindow capture without raising window")
    cap_p.add_argument("--window-id", type=int, required=True)
    cap_p.add_argument("--out", required=True)
    park_p = pc_sub.add_parser("park", help="Park window into non-intrusive strip via SWP_NOACTIVATE")
    park_p.add_argument("--window-id", type=int, required=True)
    park_p.add_argument("--slot", choices=list(PARKING_SLOTS.keys()), default="right-strip")
    obs_p = pc_sub.add_parser("uia-observe", help="Inspect structured UIA elements for a window")
    obs_p.add_argument("--pid", type=int, default=1)
    obs_p.add_argument("--window-id", type=int, required=True)

    # cloudpc (dual-track Cloud PC management)
    cpc_parser = sub.add_parser("cloudpc", help="Dual-track Cloud PC management (A2A + offscreen ToDesk)")
    cpc_sub = cpc_parser.add_subparsers(dest="cloudpc_cmd", required=True)
    cpc_sub.add_parser("status", help="Inspect ToDesk + Cloud PC A2A queue status")
    ccap_p = cpc_sub.add_parser("capture", help="Capture Cloud PC ToDesk screen offscreen")
    ccap_p.add_argument("--out", required=True)
    enq_p = cpc_sub.add_parser("enqueue", help="Enqueue bounded command to Cloud PC A2A inbox")
    enq_p.add_argument("--task-id", required=True)
    enq_p.add_argument("--cmd", required=True)
    enq_p.add_argument("--timeout", type=int, default=120)

    # todesk
    todesk_parser = sub.add_parser("todesk", help="ToDesk remote desktop inspection and staging")
    todesk_sub = todesk_parser.add_subparsers(dest="todesk_cmd", required=True)
    todesk_sub.add_parser("status", help="Get live ToDesk window and connection telemetry")
    stage_parser = todesk_sub.add_parser("stage-bundle", help="Stage sanitized bundle stripping plaintext secrets")
    stage_parser.add_argument("--source", required=True, help="Directory containing .tar.enc and .sha256.txt")
    stage_parser.add_argument("--output", required=True, help="Output sanitized .zip path")
    clip_parser = todesk_sub.add_parser("clipboard", help="Copy files into Windows CF_HDROP clipboard")
    clip_parser.add_argument("files", nargs="+", help="File paths to place on clipboard")

    # retrospect
    retro_parser = sub.add_parser("retrospect", help="Review action trajectories and generate self-iteration findings")
    retro_parser.add_argument("--limit", type=int, default=200, help="Max recent trajectory records to analyze")
    retro_parser.add_argument("--slow-ms", type=float, default=500.0, help="Latency threshold in ms for slow action warning")

    args = parser.parse_args(argv)
    try:
        if args.command == "retrospect":
            rep = run_retrospect(limit=args.limit, slow_threshold_ms=args.slow_ms)
            print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "normalize":
            wsize = (
                (args.window_width, args.window_height)
                if args.window_width and args.window_height
                else None
            )
            action = normalize_action(
                args.source,
                args.action,
                target=Target(kind="window", pid=args.pid, window_id=args.window_id),
                action_id=args.action_id,
                agent_id=args.agent_id,
                delivery_mode=args.delivery_mode,
                coord_space=args.coord_space,
                window_size=wsize,
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

        if args.command == "pc":
            if args.pc_cmd == "list-windows":
                wins = [asdict(w) for w in Win32Backend.list_windows(args.filter)]
                print(json.dumps({"windows": wins, "count": len(wins)}, ensure_ascii=False, indent=2))
                return 0
            if args.pc_cmd == "capture":
                cap = capture_window_offscreen(args.window_id, args.out)
                print(json.dumps(asdict(cap), ensure_ascii=False, indent=2))
                return 0 if cap.ok else 1
            if args.pc_cmd == "park":
                res = park_window_noactivate(args.window_id, args.slot)
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0 if res.get("ok") else 1
            if args.pc_cmd == "uia-observe":
                obs = observe_window_uia(Target(kind="window", pid=args.pid, window_id=args.window_id))
                print(json.dumps(obs, ensure_ascii=False, indent=2))
                return 0

        if args.command == "cloudpc":
            if args.cloudpc_cmd == "status":
                print(json.dumps(cloudpc_status(), ensure_ascii=False, indent=2))
                return 0
            if args.cloudpc_cmd == "capture":
                res = capture_cloudpc_offscreen(args.out)
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0 if res.get("ok") else 1
            if args.cloudpc_cmd == "enqueue":
                res = enqueue_cloudpc_a2a(args.task_id, args.cmd, timeout_s=args.timeout)
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0

        if args.command == "todesk":
            if args.todesk_cmd == "status":
                telem = get_todesk_telemetry()
                print(json.dumps(asdict(telem), ensure_ascii=False, indent=2))
                return 0
            if args.todesk_cmd == "stage-rt":
                res = prepare_sanitized_bundle(args.source, args.output)
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
            elif backend_choice == "uia":
                backend = UIABackend()
            elif backend_choice == "win32":
                backend = Win32Backend(allow_foreground=args.allow_foreground)
            elif backend_choice == "cua-driver":
                backend = CuaDriverBackend(executable=args.driver, allow_foreground=args.allow_foreground)
            else:  # composite: Semantic UIA -> Win32 PostMessage/SendMessage -> Cua Driver
                win32_b = Win32Backend(allow_foreground=args.allow_foreground)
                uia_b = UIABackend(win32_fallback=win32_b)
                cua_b = CuaDriverBackend(executable=args.driver, allow_foreground=args.allow_foreground)
                backend = CompositeBackend([uia_b, win32_b, cua_b])

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
