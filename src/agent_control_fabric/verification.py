"""Deterministic verification helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import Action, Target


@dataclass
class VerificationResult:
    status: str
    details: dict[str, Any] = field(default_factory=dict)


class ObservationVerifier:
    """Verify simple expected predicates from a caller-provided observation."""

    def __init__(self, observe: Callable[[Target], Mapping[str, Any]]):
        self._observe = observe

    def verify(self, action: Action) -> VerificationResult:
        try:
            observation = self._observe(action.target)
        except Exception as exc:
            return VerificationResult("unknown", {"error": str(exc)})
        if not isinstance(observation, Mapping):
            return VerificationResult("unknown", {"reason": "observation is not an object"})
        results = [self._predicate(observation, expected) for expected in action.expected]
        if not results:
            return VerificationResult("unknown", {"reason": "no predicates"})
        if all(item["status"] == "satisfied" for item in results):
            return VerificationResult("satisfied", {"predicates": results})
        if any(item["status"] == "unsatisfied" for item in results):
            return VerificationResult("unsatisfied", {"predicates": results})
        return VerificationResult("unknown", {"predicates": results})

    @staticmethod
    def _predicate(observation: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
        if expected.get("kind", "element") == "window":
            expected_exists = expected.get("exists")
            if expected_exists is None:
                return {"status": "unknown", "reason": "window.exists missing"}
            actual = observation.get("window_exists")
            if actual is None:
                return {"status": "unknown", "reason": "window state absent"}
            return {
                "status": "satisfied" if bool(actual) is bool(expected_exists) else "unsatisfied",
                "actual": actual,
                "expected": expected_exists,
            }

        selector = expected.get("selector") or {}
        elements = observation.get("elements")
        if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
            return {"status": "unknown", "reason": "element collection absent"}
        matches = []
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            if "role" in selector and element.get("role") != selector["role"]:
                continue
            label = str(element.get("label", element.get("name", "")))
            if "label_contains" in selector and selector["label_contains"] not in label:
                continue
            matches.append(element)
        if expected.get("exists") is True and not matches:
            return {"status": "unsatisfied", "reason": "no matching element"}
        if expected.get("exists") is True:
            element = matches[0]
            if "value_equals" in expected and element.get("value") != expected["value_equals"]:
                return {"status": "unsatisfied", "reason": "value mismatch"}
            if "enabled" in expected and element.get("enabled") != expected["enabled"]:
                return {"status": "unsatisfied", "reason": "enabled mismatch"}
            return {"status": "satisfied", "match_count": len(matches)}
        return {"status": "unknown", "reason": "only exists=true is provable"}


class StaticVerifier:
    """Test and integration helper that returns a fixed verification result."""

    def __init__(self, status: str, details: Mapping[str, Any] | None = None):
        self.status = status
        self.details = dict(details or {})

    def verify(self, action: Action) -> VerificationResult:
        return VerificationResult(self.status, dict(self.details))
