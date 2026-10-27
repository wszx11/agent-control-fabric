# Agent Control Fabric (v0.4.0)

A lightweight, dependency-free, focus-safe execution fabric combining visual/native
planners (UI-TARS, Agent S, UFO2) with multi-agent window-routed computer control,
offscreen window capture, cross-process mutual-exclusion locks, automated
trajectory retrospect, and dual-track Cloud PC (ToDesk + A2A) management.

**Why it exists**: agents that share a desktop with a human must never steal focus,
move the physical mouse, or fight each other over the same window. ACF routes every
action to a specific window handle and delivers it in the background — the human
keeps typing, the agents keep working.

## Core Capabilities (v0.4.0)

- **Canonical Action Protocol (`normalize.py`)**:
  - Normalizes UI-TARS (`coord_space="normalized_1000"` auto-scaling to client pixels), Agent S, and UFO2 semantic selectors (`selector={"name": ..., "role": ...}`).
- **Multi-Agent Non-Blocking Concurrency (`router.py` + `locks.py`)**:
  - Independent windows execute **concurrently** across threads and processes; actions targeting the same `(pid, window_id)` are strictly serialized via in-process locks and atomic cross-process file leases (`ResourceLock`, TOCTOU-hardened + dead-owner takeover).
- **Focus-Safe Composite Backends (`uia_backend.py`, `win32_backend.py`, `backends.py`)**:
  - `UIABackend`: UFO2-style semantic Windows UI Automation (`InvokePattern`, `ValuePattern`, `SelectionItemPattern`, `TogglePattern`) with `ScreenToClient` fallback.
  - `Win32Backend`: Per-Monitor DPI-aware `PostMessageW` / `SendMessageW` (auto-upgraded for Sciter `H-SMILE-FRAME`) supporting `click`, `double_click`, `right_click`, `middle_click`, `type`, `press`/`hotkey`, vertical & horizontal `scroll`, and interpolated background `drag` — restoring iconic windows via `SW_SHOWNOACTIVATE` so foreground focus and the physical OS mouse cursor are **never stolen**.
  - `CuaDriverBackend`: Calls Cua Driver with `delivery_mode=background` fail-closed semantics.
- **Offscreen Capture & Parallel-Screen Layout (`capture.py`, `layout.py`)**:
  - `capture_window_offscreen`: Captures occluded background windows via Win32 `PrintWindow(PW_RENDERFULLCONTENT)` and a pure-stdlib PNG encoder.
  - `park_window_noactivate` & `UserInputGuard`: Places agent windows into side strips via `SWP_NOACTIVATE` and verifies foreground/cursor preservation via `AttachThreadInput`.
- **Dual-Track Cloud PC & ToDesk Control (`cloudpc.py`, `todesk.py`)**:
  - Track A: Sanitized RT bundle staging (rejecting `*.password.txt`) + Cloud PC A2A `inbox.jsonl` task dispatch.
  - Track B: Offscreen `PrintWindow` capture of ToDesk remote desktop + window-local background input routing.
- **Automated Trajectory Retrospect (`retrospect.py`)**:
  - Atomic `O_APPEND` trajectory logging (`trajectories.jsonl`) and `acf retrospect` bottleneck/failure-code diagnostics for continuous self-iteration.

## Install

```bash
pip install -e .          # zero runtime dependencies, Python >= 3.11, Windows for live control
```

## Quick Start

```bash
# Run full unit test suite (27 tests, zero external dependencies)
PYTHONPATH=src python -m unittest discover -s tests -v

# Local PC non-intrusive management
PYTHONPATH=src python -m agent_control_fabric pc list-windows
PYTHONPATH=src python -m agent_control_fabric pc capture --window-id 12345 --out window.png
PYTHONPATH=src python -m agent_control_fabric pc park --window-id 12345 --slot right-strip

# Dual-track Cloud PC (ToDesk + A2A) management
PYTHONPATH=src python -m agent_control_fabric cloudpc status
PYTHONPATH=src python -m agent_control_fabric cloudpc capture --out cloudpc_screen.png

# Review execution trajectories & generate next self-iteration backlog
PYTHONPATH=src python -m agent_control_fabric retrospect
```

## Python API

```python
from agent_control_fabric import Action, Target, ActionRouter, Win32Backend

router = ActionRouter(Win32Backend())
act = Action(
    action_id="demo-1",
    agent_id="my-agent",
    source="ufo2",
    kind="click",
    target=Target(kind="window", pid=1234, window_id=5678),
    parameters={"x": 40, "y": 12},
    delivery_mode="background",   # foreground delivery is blocked by default
)
result = router.route(act)
print(result.status)              # executed / failed / blocked
```

Two agents driving two different windows run concurrently; two agents fighting over
the same window are serialized by `ResourceLock` — see `examples/two-agents.json`
and run it with `acf run examples/two-agents.json --dry-run`.

## Safety Model

- `delivery_mode="foreground"` is **rejected by default** in every backend.
- Window restore uses `SW_SHOWNOACTIVATE` + `HWND_BOTTOM` — never `SetForegroundWindow`.
- `UserInputGuard` snapshots foreground window and cursor position, and verifies both
  are unchanged after every guarded block.
- Sanitized bundle staging refuses any file matching `*password*`, `*.key`, `*.pem`, `*.env`.

## Status

v0.4.0 — battle-tested in daily multi-agent desktop operation (500+ recorded
trajectory actions, 98% success rate). Windows-first; UIA/Win32 backends require
Windows, other modules are pure stdlib and cross-platform.

## License

MIT — see [LICENSE](LICENSE). Third-party component notes in [THIRD_PARTY.md](THIRD_PARTY.md).
