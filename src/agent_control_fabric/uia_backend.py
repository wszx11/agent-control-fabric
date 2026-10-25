"""UFO2-style Windows UI Automation (UIA) semantic backend and state observer.

Allows agents to address controls inside a specific `window_id` semantically
by `name`, `automation_id`, or `control_type` instead of fragile pixel coords:
1. Tries native UIA background patterns first (`InvokePattern`, `ValuePattern`,
   `SelectionItemPattern`, `TogglePattern`) — zero focus steal, zero cursor move.
2. If a custom-drawn framework (e.g. Sciter `H-SMILE-FRAME`) rejects pattern invoke,
   computes exact window-client coordinates from `BoundingRectangle` and routes
   via `Win32Backend` (`SendMessageW` / `PostMessageW`).
3. Provides `observe_window_uia(target)` compatible with `ObservationVerifier`
   for OpenAdapt-style deterministic post-action verification.
"""

from __future__ import annotations

import ctypes
from typing import Any, Dict, List, Optional

from .models import Action, BackendResult, Target
from .win32_backend import IS_WINDOWS, Win32Backend, ensure_dpi_aware, user32, wt

ensure_dpi_aware()


def _load_uia() -> Any:
    try:
        import uiautomation as auto
        return auto
    except Exception:
        return None


def observe_window_uia(target: Target, *, max_depth: int = 12) -> Dict[str, Any]:
    """Walk the UIA tree of `target.window_id` and return structured elements for ObservationVerifier."""
    hwnd = int(target.window_id or 0)
    if not hwnd or not user32.IsWindow(hwnd):
        return {"window_exists": False, "elements": []}

    auto = _load_uia()
    if auto is None:
        return {"window_exists": True, "elements": [], "uia_available": False}

    elements: List[Dict[str, Any]] = []
    try:
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(hwnd)
            for ctrl, depth in auto.WalkControl(root, maxDepth=max_depth):
                name = (ctrl.Name or "").strip()
                aid = (ctrl.AutomationId or "").strip()
                role = (ctrl.ControlTypeName or "").replace("Control", "")
                val = None
                try:
                    vp = ctrl.GetValuePattern()
                    if vp:
                        val = vp.Value
                except Exception:
                    pass
                selected = None
                try:
                    sp = ctrl.GetSelectionItemPattern()
                    if sp:
                        selected = bool(sp.IsSelected)
                except Exception:
                    pass
                rect = ctrl.BoundingRectangle
                bounds = (
                    {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}
                    if rect
                    else None
                )
                elements.append(
                    {
                        "depth": depth,
                        "role": role,
                        "label": name,
                        "automation_id": aid,
                        "enabled": bool(ctrl.IsEnabled),
                        "value": val,
                        "selected": selected,
                        "bounds": bounds,
                    }
                )
    except Exception as exc:
        return {"window_exists": True, "elements": elements, "error": str(exc)}

    return {"window_exists": True, "elements": elements, "uia_available": True}


class UIABackend:
    """Semantic Windows UIA backend with Win32 client-coord fallback."""

    name = "uia"

    def __init__(self, *, win32_fallback: Optional[Win32Backend] = None, max_depth: int = 18):
        self.win32 = win32_fallback or Win32Backend()
        self.max_depth = max_depth

    def _find_control(
        self,
        auto: Any,
        hwnd: int,
        selector: Dict[str, Any],
    ) -> Any:
        want_name = selector.get("name")
        want_contains = selector.get("name_contains")
        want_id = selector.get("automation_id")
        want_role = selector.get("role")
        root = auto.ControlFromHandle(hwnd)
        for ctrl, _ in auto.WalkControl(root, maxDepth=self.max_depth):
            cname = (ctrl.Name or "").strip()
            cid = (ctrl.AutomationId or "").strip()
            crole = (ctrl.ControlTypeName or "").replace("Control", "")
            if want_name is not None and cname != want_name:
                continue
            if want_contains is not None and want_contains not in cname:
                continue
            if want_id is not None and cid != want_id:
                continue
            if want_role is not None and crole.lower() != str(want_role).lower().replace("control", ""):
                continue
            return ctrl
        return None

    def execute(self, action: Action) -> BackendResult:
        if action.target.kind != "window" or not action.target.window_id:
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "target_not_supported", "message": "UIABackend requires a window target"},
            )
        if action.delivery_mode == "foreground":
            return BackendResult(
                status="blocked",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "foreground_not_allowed", "message": "UIABackend is strictly background-only"},
            )

        selector = action.parameters.get("selector")
        if not isinstance(selector, dict):
            # No semantic selector supplied -> delegate to Win32Backend if coordinates exist
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "selector_required", "message": "no semantic selector in parameters"},
            )

        hwnd = int(action.target.window_id)
        if not user32.IsWindow(hwnd):
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "target_unavailable", "hwnd": hwnd},
            )

        auto = _load_uia()
        if auto is None:
            return BackendResult(
                status="failed",
                action_id=action.action_id,
                backend=self.name,
                details={"code": "uia_unavailable", "message": "uiautomation package not installed"},
            )

        with auto.UIAutomationInitializerInThread():
            ctrl = self._find_control(auto, hwnd, selector)
            if ctrl is None:
                return BackendResult(
                    status="failed",
                    action_id=action.action_id,
                    backend=self.name,
                    details={"code": "element_not_found", "selector": selector},
                )

            if action.kind == "type":
                text = str(action.parameters.get("content", action.parameters.get("text", "")))
                try:
                    vp = ctrl.GetValuePattern()
                    if vp:
                        vp.SetValue(text)
                        return BackendResult(
                            status="executed",
                            action_id=action.action_id,
                            backend=self.name,
                            details={"method": "ValuePattern.SetValue", "chars": len(text), "selector": selector},
                        )
                except Exception:
                    pass

            if action.kind in ("click", "double_click"):
                # 1. Try InvokePattern
                if action.kind == "click":
                    try:
                        ip = ctrl.GetInvokePattern()
                        if ip:
                            ip.Invoke()
                            return BackendResult(
                                status="executed",
                                action_id=action.action_id,
                                backend=self.name,
                                details={"method": "InvokePattern.Invoke", "selector": selector},
                            )
                    except Exception:
                        pass
                    # 2. Try SelectionItemPattern (e.g. RadioButton / ListItem)
                    try:
                        sp = ctrl.GetSelectionItemPattern()
                        if sp:
                            sp.Select()
                            if bool(sp.IsSelected):
                                return BackendResult(
                                    status="executed",
                                    action_id=action.action_id,
                                    backend=self.name,
                                    details={"method": "SelectionItemPattern.Select", "selector": selector},
                                )
                    except Exception:
                        pass
                    # 3. Try TogglePattern (CheckBox)
                    try:
                        tp = ctrl.GetTogglePattern()
                        if tp:
                            tp.Toggle()
                            return BackendResult(
                                status="executed",
                                action_id=action.action_id,
                                backend=self.name,
                                details={"method": "TogglePattern.Toggle", "selector": selector},
                            )
                    except Exception:
                        pass

                # 4. Fallback: convert screen center to exact window-client coords via ScreenToClient
                rect = ctrl.BoundingRectangle
                if not rect or rect.width() <= 0 or rect.height() <= 0:
                    parent = ctrl.GetParentControl()
                    rect = parent.BoundingRectangle if parent else None
                if rect and rect.width() > 0 and rect.height() > 0:
                    pt = wt.POINT(int((rect.left + rect.right) // 2), int((rect.top + rect.bottom) // 2))
                    if hasattr(user32, "ScreenToClient") and user32.ScreenToClient(hwnd, ctypes.byref(pt)):
                        cx, cy = int(pt.x), int(pt.y)
                    else:
                        wr = wt.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(wr))
                        cx = int((rect.left + rect.right) // 2 - wr.left)
                        cy = int((rect.top + rect.bottom) // 2 - wr.top)
                    win32_act = Action(
                        action_id=action.action_id,
                        agent_id=action.agent_id,
                        source=action.source,
                        kind=action.kind,
                        target=action.target,
                        parameters={"x": cx, "y": cy, "sync": True},
                        delivery_mode="background",
                    )
                    res = self.win32.execute(win32_act)
                    res.details["uia_resolved_selector"] = selector
                    return res

        return BackendResult(
            status="failed",
            action_id=action.action_id,
            backend=self.name,
            details={"code": "uia_dispatch_failed", "selector": selector},
        )
