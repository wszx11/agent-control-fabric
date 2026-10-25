"""Public package surface for agent-control-fabric."""

__version__ = "0.2.0"

from .backends import CompositeBackend, CuaDriverBackend, DryRunBackend, FunctionBackend
from .locks import ResourceLock, active_keys, peek
from .models import Action, ActionValidationError, BackendResult, Target
from .normalize import AgentSAdapter, UFO2Adapter, UITarsAdapter, normalize_action
from .router import ActionRouter
from .todesk import copy_files_to_clipboard, get_todesk_telemetry, prepare_sanitized_rt_bundle
from .verification import ObservationVerifier, StaticVerifier, VerificationResult
from .win32_backend import Win32Backend, WindowInfo

__all__ = [
    "__version__",
    "Action",
    "ActionRouter",
    "ActionValidationError",
    "AgentSAdapter",
    "BackendResult",
    "CompositeBackend",
    "CuaDriverBackend",
    "DryRunBackend",
    "FunctionBackend",
    "ObservationVerifier",
    "ResourceLock",
    "StaticVerifier",
    "Target",
    "UFO2Adapter",
    "UITarsAdapter",
    "VerificationResult",
    "Win32Backend",
    "WindowInfo",
    "active_keys",
    "copy_files_to_clipboard",
    "get_todesk_telemetry",
    "normalize_action",
    "peek",
    "prepare_sanitized_rt_bundle",
]
