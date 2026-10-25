"""Public package surface for agent-control-fabric (v0.4.0)."""

__version__ = "0.4.0"

from .backends import CompositeBackend, CuaDriverBackend, DryRunBackend, FunctionBackend
from .capture import CaptureResult, capture_window_offscreen
from .cloudpc import (
    capture_cloudpc_offscreen,
    cloudpc_status,
    enqueue_cloudpc_a2a,
    route_cloudpc_window_action,
)
from .layout import InputSnapshot, PARKING_SLOTS, UserInputGuard, park_window_noactivate, sample_input_state
from .locks import ResourceLock, active_keys, peek
from .models import Action, ActionValidationError, BackendResult, Target
from .normalize import AgentSAdapter, UFO2Adapter, UITarsAdapter, normalize_action
from .retrospect import RetrospectReport, record_trajectory, run_retrospect
from .router import ActionRouter
from .todesk import copy_files_to_clipboard, get_todesk_telemetry, prepare_sanitized_rt_bundle
from .uia_backend import UIABackend, observe_window_uia
from .verification import ObservationVerifier, StaticVerifier, VerificationResult
from .win32_backend import Win32Backend, WindowInfo

__all__ = [
    "__version__",
    "Action",
    "ActionRouter",
    "ActionValidationError",
    "AgentSAdapter",
    "BackendResult",
    "CaptureResult",
    "CompositeBackend",
    "CuaDriverBackend",
    "DryRunBackend",
    "FunctionBackend",
    "InputSnapshot",
    "ObservationVerifier",
    "PARKING_SLOTS",
    "ResourceLock",
    "RetrospectReport",
    "StaticVerifier",
    "Target",
    "UFO2Adapter",
    "UIABackend",
    "UITarsAdapter",
    "UserInputGuard",
    "VerificationResult",
    "Win32Backend",
    "WindowInfo",
    "active_keys",
    "capture_cloudpc_offscreen",
    "capture_window_offscreen",
    "cloudpc_status",
    "copy_files_to_clipboard",
    "enqueue_cloudpc_a2a",
    "get_todesk_telemetry",
    "normalize_action",
    "observe_window_uia",
    "park_window_noactivate",
    "peek",
    "prepare_sanitized_rt_bundle",
    "record_trajectory",
    "route_cloudpc_window_action",
    "run_retrospect",
    "sample_input_state",
]
