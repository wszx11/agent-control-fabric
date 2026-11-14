# Changelog

All notable changes to Agent Control Fabric are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.2] - 2026-11-14

### Added

- **No-foreground policy layer** (`policy.py`): `NO_FOREGROUND_ENV`
  (`ACF_NO_FOREGROUND`), `is_foreground_request`,
  `check_foreground_allowed`, `is_no_foreground_enforced`.
- `ActionRouter` constructor gains `no_foreground=True` and `focus_guard=True`;
  blocked foreground requests return `backend="router-no-foreground"` with
  `code="foreground_not_allowed"`.
- Win32 / Cua / UIA backends all route foreground checks through
  `check_foreground_allowed`; `UIABackend` gains an `allow_foreground` parameter.
- `UserInputGuard.summary()` now reports focus-drift telemetry:
  `drifted`, `drifted_restored`, `drifted_hwnd`.
- `acf run --no-foreground` (takes precedence over `--allow-foreground`);
  setting `ACF_NO_FOREGROUND=1` hard-blocks all foreground / window-raising
  actions at environment level.

## [0.5.1] - 2026-11-02

### Fixed

- Win32 guard hardening: process-name channel, empty-title coverage and
  escape cleanup in the user-document input guard (+5 unit tests).
- Retrospect: exclude test fixtures from business metrics and isolate test
  lock directories (`run_retrospect` filters fixture records; the report
  gains `excluded_test_records` / `real_records_analyzed`).
- Portability: the off-Windows import guard is asserted in-suite so the
  platform invariant cannot rot.
