"""Public package surface."""

from .backends import CuaDriverBackend, DryRunBackend, FunctionBackend
from .models import Action, ActionValidationError, BackendResult, Target
from .normalize import AgentSAdapter, UFO2Adapter, UITarsAdapter, normalize_action
from .router import ActionRouter
from .verification import ObservationVerifier, StaticVerifier, VerificationResult

__all__ = [
    "Action",
    "ActionRouter",
    "ActionValidationError",
    "AgentSAdapter",
    "BackendResult",
    "CuaDriverBackend",
    "DryRunBackend",
    "FunctionBackend",
    "ObservationVerifier",
    "StaticVerifier",
    "Target",
    "UFO2Adapter",
    "UITarsAdapter",
    "VerificationResult",
    "normalize_action",
]
