# Agent Control Fabric

A small, dependency-free execution fabric for combining visual planners with
focus-safe, window-routed computer control.

The project deliberately integrates upstream systems through adapters and
protocols. It does not copy UI-TARS, Agent S, UFO2, Cua Driver, or OpenAdapt
source code into this repository.

## What this provides

- One canonical action protocol for UI-TARS, Agent S, and UFO2-style outputs.
- Explicit routing by window target (`pid` + `window_id`).
- Cua Driver execution with `delivery_mode=background` by default.
- Per-window locks: different windows can run concurrently; one window is
  serialized to prevent interleaved text and stale coordinates.
- A fail-closed background boundary: `background_unavailable` is reported as an
  escalation signal and never silently upgraded to foreground input.
- Deterministic predicate verification where `unknown` is not success.
- Dry-run mode and standard-library tests that do not touch the desktop.

## Architecture

```text
planner output
  UI-TARS / Agent S / UFO2
          |
          v
  normalizer -> canonical Action
          |
          v
  ActionRouter -- per-target lock --> backend
          |                              |
          |                              +-- CuaDriverBackend (window UIA/Win32)
          |                              +-- CdpBackend (integration boundary)
          |                              +-- ReplayBackend (integration boundary)
          |
          +--> verifier (structured state, DOM, or replay evidence)
```

## Safety defaults

1. Window actions require an exact target with both `pid` and `window_id`.
2. Cua Driver calls use `background` unless a caller explicitly requests
   `foreground` and the backend was configured with `allow_foreground=True`.
3. A Cua `background_unavailable` response stops the action. The caller must
   choose an isolated VM/RDP backend or make an explicit, auditable escalation.
4. The router never uses desktop-wide coordinates for a window action.
5. No credentials, cookies, browser profiles, or local runtime state belong in
   this repository.

## Quick start

Run the test suite without installing dependencies:

```text
python -m unittest discover -s tests -v
```

Normalize a planner action:

```text
python -m agent_control_fabric normalize \
  --source ui-tars \
  --action "click(start_box='[100, 200, 180, 260]')" \
  --pid 1234 --window-id 5678 --agent-id visual-01
```

Dry-run a JSON action file:

```text
python -m agent_control_fabric run examples/two-agents.json --dry-run
```

Execute against the local Cua Driver only after the target window and expected
state have been independently inspected:

```text
python -m agent_control_fabric run action.json --driver cua-driver
```

## Integration boundaries

- `UI-TARS`: its serialized action space is accepted by `UITarsAdapter`.
- `Agent S`: common dictionary action fields are accepted by `AgentSAdapter`.
- `UFO2`: host/app action envelopes are accepted by `UFO2Adapter`.
- `Cua Driver`: the subprocess adapter calls its public `call` interface and
  passes exact window routing fields.
- `OpenAdapt`: deterministic replay can be registered as a backend; this
  repository does not reimplement its recorder or replay engine.
- `CDP/Playwright`: browser backends can be registered without changing the
  canonical protocol.

The adapters are intentionally thin. Model inference, browser sessions, and
application-specific verification remain replaceable services.
