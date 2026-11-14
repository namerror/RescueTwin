# RescueTwin implementation and demo guide

## Current status and first gate

The repository includes a synthetic grid simulator, a local Three.js scenario editor,
REST/WebSocket interfaces, a deterministic mission policy, an optional Gemini tool adapter,
and an independent YOLO detector adapter. The YOLO adapter is not wired into a rendered RGB-D
pipeline. The runtime fixture is not a sponsor property. Real scene preparation, verified
exits, camera inference, uncertainty growth/forecasting, and validated hosted inference
remain incomplete. Physical motion is grid-based, not a continuous rigid-body simulation.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8000
# separate terminal
cd frontend
npm install
npm run dev
# checks (from repository root, activated environment)
python3 -m pytest -q
# frontend check: cd frontend && npm run build
```

Optional extras: `pip install -e '.[agent,vision]'`. Export `GEMINI_API_KEY` and a
verified `GEMINI_MODEL` before server startup to enable hosted decisions. Backend
credentials are never included in browser configuration. Source preparation starts with
`python3 scripts/inspect_sponsor.py MODEL` and `python3 scripts/fetch_matterport.py MODEL`.
The latter saves ignored source metadata; it does not certify exits or load a real scene.

Before coding in parallel, read [architecture](ARCHITECTURE.md), [MVP](MVP.md), and
[AGENTS.md](../AGENTS.md). Confirm the scene candidate, two floor-level exits, coordinate
transform, and shared contracts. Test a rendered avatar with actual person-detection
weights and verify Gemini image/tool capabilities and credentials. Do not hard-code
unverified model names from planning as guaranteed available releases.

## Ownership and 24–36 hour milestones

| Owner | Deliverable |
|---|---|
| Physics teammate | Hazard fields, wall/door propagation, deterministic reset, sensor/visual field agreement |
| Autonomy teammate | Controller, perception integration, belief state, routing, validated agent tools, metrics |
| Scene/UI teammate | Sponsor joins, scene prep, scenario editor, rendering/camera, dashboard, pitch |

| Elapsed time | Integration gate |
|---|---|
| 0–3 h | Inspect sponsor scene/exits; lock types/coordinates; verify detector and hosted reasoning tool call |
| 3–10 h | Editable scene + collision movement + camera + hazard fields; evacuate one survivor end to end |
| 10–18 h | Belief updates, real tool results, second survivor, risk replanning, timeout/no-route handling |
| 18–24 h | Paired reports, scenario save/load, acceptance checks, documentation, recorded backup demo |
| 24–36 h | Rehearse/reliability fixes and visual polish; only then optional FAR/Vexcel |

Integrate small contract-compatible changes at each gate. The physics teammate's
existing work should be adapted through the agreed HazardFrame interface, not replaced
without inspection. One laptop should support the baseline; local RTX 3060 inference
is an initial target. Remote desktop GPUs are separate devices, not a 28 GB memory
pool. Benchmark throughput; no training or rented GPU is required by this design.

## Build sequence and verification

1. **Scene and provenance:** discover the ignored kit with `rg --files --hidden
   --no-ignore data/costar-hackathon-kit`; use documented key + model joins. Record
   scale, transforms, occupancy, exits, capacity field lineage, and unknown values.
   Inspect endpoints manually. Keep sponsor assets ignored.
2. **Authoritative loop:** implement simulation time, controls/reset, hazard adapter,
   swept collision and simple follower; unit-check wall isolation, doorway spread,
   deterministic hazard reset, and follower clearance. Use normalized hazard units.
3. **Perception boundary:** synchronize rendered RGB/depth/pose; run person inference,
   project detections, confirm/deduplicate, and enforce smoke/occlusion. Test no oracle
   entity information reaches belief state. Label fixture detectors explicitly.
4. **Autonomy:** implement distance-only and risk-aware A*, versioned observations,
   bounded tools, event-driven reasoning, stale-result rejection, and route/follower
   safety checks. Test hazard invalidation, no-route outcomes, malformed calls, and
   network timeouts while navigation remains independent of model latency.
5. **Operator workflow:** editable placements, validation, frozen run inputs,
   seed/save/load, truth/belief toggle, timeline, and comparable result export. Use
   the full acceptance checklist in MVP.md before declaring baseline complete.

Proposed API groups: scene listing/loading; scenario validation/save/load; run
start/pause/reset; paired comparison; operator state stream; agent observation stream.
Finalize concrete payloads against architecture contracts before implementing clients.
Do not let operator state become the agent's observation source. Reports should retain
configuration, seed, source IDs, model/version, timing, events, and metric definitions.

When implementation exists, record tested Python/Node versions, dependency installation,
asset preparation, environment variable names, backend/frontend startup, test commands,
and expected health signal here. Never include secret values or signed download URLs.

## FAR experiment and scope control

Start FAR only after the baseline acceptance gate passes. Timebox the entire attempt
to two hours, verify current ROS/upstream compatibility, and implement it behind the
existing planner interface. Accept it only if it preserves observed-risk constraints,
follower validation, reset behavior, and the complete mission. Otherwise retain A*;
no hackathon ROS migration is required. TARE ideas do not require importing TARE.

If behind schedule, cut FAR, Vexcel, advanced effects, and dense stress-run polish
first. Preserve editable scenarios, actual image perception, changing-hazard routing,
agent action/results, and a full rescue workflow. Keep unresolved cases visible.

## Demo and handoff

Prepare one repeatable scene and a scenario in which a changing observed hazard
invalidates a route. Show a scenario edit, search, image detection, evidence-driven
action, route change, and evacuation or explicit unresolved outcome. Run both planners
from frozen inputs; show failures and timing as well as gains. Record a backup using
the real application once working; label recordings and synthetic fixtures accurately.

Pitch: existing property data becomes an interactive rehearsal for building safety
teams. Explain how CoStar capacity affects the scenario and Matterport changes spatial
decisions. State single-floor egress assumptions, synthetic hazard limitations, and
why comparison gains cannot isolate LLM contribution. Amazon alignment comes from
useful multi-step action and adaptation; it does not require an unplanned AWS migration.

Final handoff should include verified setup/check commands, asset provenance, known
failures, demonstration configuration and recording, metric definitions, and outstanding
work. Never describe the application as complete solely because these documents exist.

## Verification recorded for this baseline

- Python 3.12 / Node 24: 13 backend tests pass, including HTTP controls, observation
  privacy, WebSocket snapshots, deterministic hazards, routing, interlocks, escort,
  stale/hidden agent goals, placement validation, and exact sponsor joins.
- `npm run build` passes TypeScript checking and Vite production compilation.
- Default synthetic mission evacuates both survivors; this is fixture evidence only.
- `git diff --check` passes.
- The frontend build reports a non-blocking bundle-size warning; TestClient reports
  an upstream httpx deprecation warning. No browser surface was available for visual QA.
- Hosted Gemini calls, pretrained detector weights/GPU execution, and real scene loading
  have not been verified. These checks do not satisfy full MVP acceptance yet.
