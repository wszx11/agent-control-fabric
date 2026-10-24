# Third-party integration boundaries

Agent Control Fabric is an orchestration layer. It communicates with upstream
projects through documented action formats or process/API boundaries and does
not vendor their source code.

| Component | Role here | Boundary |
|---|---|---|
| UI-TARS | visual planner/action producer | serialized action adapter |
| Agent S | planner/action producer | dictionary action adapter |
| UFO2 | Windows host/app planner | host/app action envelope adapter |
| Cua Driver | Windows execution backend | `cua-driver call <tool> <json>` subprocess boundary |
| OpenAdapt | deterministic replay/verification backend | registered backend protocol |
| CDP/Playwright | browser execution backend | registered backend protocol |

Before distributing a build that bundles or links any upstream implementation,
review that project's current license and NOTICE requirements separately.
