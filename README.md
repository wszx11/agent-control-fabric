# Agent Control Fabric (v0.2.0)

A lightweight, focus-safe execution fabric combining visual planners with
window-routed computer control, cross-process mutual-exclusion locks, and
remote-control desktop integrations (ToDesk).

The project deliberately integrates upstream systems through adapters and
protocols. It does not copy UI-TARS, Agent S, UFO2, Cua Driver, or OpenAdapt
source code into this repository.

## What this provides

- **Canonical Action Protocol**: Normalizes UI-TARS, Agent S, and UFO2-style outputs into structured Actions.
- **Window-Targeted Routing**: Explicit routing by window target (`pid` + `window_id`).
- **Focus-Safe Input Backends**:
  - `Win32Backend`: Native `PostMessageW` routing for background mouse clicks, text typing (`WM_CHAR`), keyboard inputs, and mouse wheel scrolling without moving the physical cursor or stealing OS focus.
  - `CuaDriverBackend`: Calls Cua Driver with `delivery_mode=background` by default.
  - `CompositeBackend`: Tries native Win32 routing with automated fallback.
- **Cross-Process Multi-Agent Locks**: File-level atomic locking (`ResourceLock`) with lease TTL, dead-owner takeover, and append-only event auditing (`events.jsonl`).
- **ToDesk Remote Integration**: Window telemetry, connection detection, sanitized RT bundle packaging (stripping plaintext credentials), and CF_HDROP clipboard sync helper.
- **Fail-Closed Boundary**: `background_unavailable` is reported as an escalation signal and never silently upgraded to foreground input.
- **Deterministic Verification**: Predicate verification where `unknown` is not success.

## Architecture

```text
planner output
  UI-TARS / Agent S / UFO2
          |
          v
  normalizer -> canonical Action
          |
          v
  ActionRouter -- cross-process locks / per-target lock --> backend
          |                                                   |
          |                                                   +-- Win32Backend (native PostMessageW)
          |                                                   +-- CuaDriverBackend (window UIA/Win32)
          |                                                   +-- CompositeBackend (fallback chain)
          |                                                   +-- DryRunBackend
          |
          +--> verifier (structured state, DOM, or replay evidence)
```

## Safety defaults

1. Window actions require an exact target with both `pid` and `window_id`.
2. Input calls use `background` unless a caller explicitly requests
   `foreground` and the backend was configured with `allow_foreground=True`.
3. Background refusals stop the action. The caller must make an explicit, auditable escalation.
4. The router never uses desktop-wide coordinates for a window action.
5. No credentials, plaintext passwords (`*.password.txt`), cookies, or browser profiles belong in
   this repository.

## Quick start

Run the test suite without installing external dependencies:

```text
python -m unittest discover -s tests -v
```

Normalize a planner action:

```text
python -m agent_control_fabric normalize \
  --source ui-tars \
  --action "click(start_box='[100, 200, 180, 260]') \
  --pid 1234 --window-id 5678 --agent-id visual-01
```

Dry-run a JSON action file:

```text
python -m agent_control_fabric run examples/two-agents.json --dry-run
```

Run with native Win32 background routing and cross-process locks:

```text
python -m agent_control_fabric run action.json --backend win32 --cross-process-locks
```

Inspect ToDesk status:

```text
python -m agent_control_fabric todesk status
```

Stage a sanitized RT container bundle (excluding plaintext password files):

```text
python -m agent_control_fabric todesk stage-rt --source "C:/Users/<you>/agentbus" --output "C:/Users/<you>/Desktop/sanitized_rt_bundle.zip"
```
