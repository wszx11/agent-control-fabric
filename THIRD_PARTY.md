# Third-party integration boundaries

Agent Control Fabric is an orchestration layer. It communicates with upstream
projects through documented action formats or process/API boundaries and does
not vendor their source code.

| Component category | Role here | Boundary |
|---|---|---|
| Visual GUI planners (coordinate/box dialects) | action producers | serialized action adapter |
| Planner DSLs (string-call + alias dialects) | action producers | dictionary action adapter |
| Accessibility-tree agents (semantic selectors) | action producers | host/app action envelope adapter |
| Cua Driver | Windows execution backend | `cua-driver call <tool> <json>` subprocess boundary |
| Deterministic replay engines | verification backend | registered backend protocol |
| Browser automation (CDP) | browser execution backend | registered backend protocol |

Before distributing a build that bundles or links any upstream implementation,
review that project's current license and NOTICE requirements separately.
