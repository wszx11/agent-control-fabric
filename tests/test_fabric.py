import threading
import time
import unittest

from agent_control_fabric import (
    ActionRouter,
    DslAdapter,
    BackendResult,
    DryRunBackend,
    ObservationVerifier,
    StaticVerifier,
    Target,
    SemanticAdapter,
    CoordinateAdapter,
    normalize_action,
)
from agent_control_fabric.backends import CuaDriverBackend


class RecordingBackend:
    name = "recording"

    def __init__(self, delay=0.03):
        self.delay = delay
        self.events = []
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0

    def execute(self, action):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.events.append(("start", action.action_id, time.monotonic()))
        time.sleep(self.delay)
        with self._guard:
            self.events.append(("end", action.action_id, time.monotonic()))
            self.active -= 1
        return BackendResult("executed", action.action_id, self.name)


class BackgroundRefusingDriver(CuaDriverBackend):
    def __init__(self):
        super().__init__(executable="unused")
        self.calls = []

    def _invoke(self, tool, payload):
        self.calls.append((tool, payload))
        return 1, "{\"error\": \"background_unavailable\"}", ""


class CanonicalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.target = Target(kind="window", pid=1234, window_id=5678)

    def test_coordinate_dialect_serialized_action_is_normalized(self):
        action = CoordinateAdapter().normalize(
            "click(start_box='[100, 200, 180, 260]')",
            target=self.target,
            action_id="ui-1",
            agent_id="vision-1",
        )
        self.assertEqual(action.kind, "click")
        self.assertEqual(action.parameters["x"], 140.0)
        self.assertEqual(action.parameters["y"], 230.0)
        self.assertEqual(action.delivery_mode, "background")

    def test_dsl_dictionary_alias_is_normalized(self):
        action = DslAdapter().normalize(
            {"type": "type", "parameters": {"text": "hello"}},
            target=self.target,
            action_id="dsl-1",
            agent_id="planner-1",
        )
        self.assertEqual(action.kind, "type")
        self.assertEqual(action.parameters, {"content": "hello"})

    def test_semantic_envelope_is_normalized(self):
        action = SemanticAdapter().normalize(
            {"action": "press", "parameters": {"keyboard_key": "enter"}},
            target=self.target,
            action_id="semantic-1",
            agent_id="host-1",
        )
        self.assertEqual(action.source, "semantic")
        self.assertEqual(action.parameters["key"], "enter")

    def test_exact_window_target_is_required(self):
        with self.assertRaises(ValueError):
            Target(kind="window", pid=1234)
        with self.assertRaises(ValueError):
            normalize_action(
                "generic",
                "press(key='enter')",
                target={"kind": "window", "pid": 1234},
            )


class RoutingTests(unittest.TestCase):
    def make_action(self, action_id, target):
        return normalize_action(
            "test",
            "wait(time=0)",
            target=target,
            action_id=action_id,
            agent_id=action_id,
        )

    def test_same_window_is_serialized(self):
        backend = RecordingBackend()
        router = ActionRouter(backend, max_workers=2)
        target = Target(kind="window", pid=1, window_id=2)
        results = router.execute_many([
            self.make_action("a", target),
            self.make_action("b", target),
        ])
        self.assertEqual([result.status for result in results], ["executed", "executed"])
        self.assertEqual(backend.max_active, 1)

    def test_different_windows_can_run_in_parallel(self):
        backend = RecordingBackend(delay=0.08)
        router = ActionRouter(backend, max_workers=2)
        actions = [
            self.make_action("a", Target(kind="window", pid=1, window_id=2)),
            self.make_action("b", Target(kind="window", pid=3, window_id=4)),
        ]
        started = time.monotonic()
        results = router.execute_many(actions)
        elapsed = time.monotonic() - started
        self.assertEqual([result.status for result in results], ["executed", "executed"])
        self.assertEqual(backend.max_active, 2)
        self.assertLess(elapsed, 0.15)

    def test_verification_unknown_is_not_success(self):
        action = normalize_action(
            "test",
            "press(key='enter')",
            target=Target(kind="window", pid=1, window_id=2),
            action_id="verify-1",
            expected=({"kind": "element", "selector": {"role": "button"}, "exists": True},),
        )
        result = ActionRouter(
            DryRunBackend(),
            ObservationVerifier(lambda target: {"other": "state"}),
        ).execute(action)
        self.assertEqual(result.status, "unknown")
        self.assertFalse(result.ok)

    def test_verification_satisfied_marks_verified(self):
        action = normalize_action(
            "test",
            "press(key='enter')",
            target=Target(kind="window", pid=1, window_id=2),
            action_id="verify-2",
            expected=({"kind": "element", "selector": {"role": "button"}, "exists": True},),
        )
        verifier = ObservationVerifier(
            lambda target: {"elements": [{"role": "button", "label": "Save", "enabled": True}]}
        )
        result = ActionRouter(DryRunBackend(), verifier).execute(action)
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.ok)


class BoundaryTests(unittest.TestCase):
    def test_background_unavailable_is_blocked_without_retry(self):
        backend = BackgroundRefusingDriver()
        action = normalize_action(
            "test",
            "click(point=(10, 20))",
            target=Target(kind="window", pid=1, window_id=2),
            action_id="blocked-1",
        )
        result = backend.execute(action)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.details["code"], "background_unavailable")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1]["delivery_mode"], "background")

    def test_foreground_is_blocked_by_default(self):
        action = normalize_action(
            "test",
            "click(point=(10, 20))",
            target=Target(kind="window", pid=1, window_id=2),
            action_id="foreground-1",
            delivery_mode="foreground",
        )
        result = CuaDriverBackend().execute(action)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.details["code"], "foreground_not_allowed")

    def test_static_unknown_verifier_is_preserved(self):
        action = normalize_action(
            "test",
            "wait(time=0)",
            target=Target(kind="window", pid=1, window_id=2),
            action_id="static-1",
            expected=({"kind": "window", "exists": True},),
        )
        result = ActionRouter(DryRunBackend(), StaticVerifier("unknown")).execute(action)
        self.assertEqual(result.status, "unknown")


if __name__ == "__main__":
    unittest.main()
