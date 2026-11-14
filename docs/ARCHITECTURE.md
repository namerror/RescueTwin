# RescueTwin architecture

## Design status and purpose

Accepted implementation target for a hackathon rehearsal tool for building safety
teams; robotics engineers are a secondary audience. This document specifies desired
behavior, not a completed or calibrated emergency-response product. Existing README
language describes a broader vision. Real sponsor geometry/records must be distinguished
from synthetic hazards, occupants, robot behavior, and any development fixture.

Use Python/FastAPI/NumPy for authoritative simulation and a TypeScript/Three.js client.
A kinematic mobile robot avoids heavy locomotion physics. Use a pretrained small YOLO
person detector and a hosted Gemini tool-calling mission agent; no end-to-end VLA
training. Model IDs are configuration, not guaranteed access: validate available
weights/API capabilities before choosing a pinned release. No pooled multi-GPU memory
assumption is permitted.

## Components and data flow

```text
CoStar + Matterport → prepared scene bundle → scenario editor
                                              ↓
                                  authoritative Python simulator
                                   ↓                     ↓
                           local sensor frames     operator truth view
                                   ↓
                       perception → belief state
                                   ↓
                        mission agent / tool calls
                                   ↓
                       action validator → A* → controller
                                   ↓
                               simulator
```

| Component | Responsibility |
|---|---|
| Scene preparation | Metric geometry, room labels, occupancy, verified exits, source provenance |
| Hazard engine | Wall-aware 2D fuel/heat/smoke evolution, deterministic reset |
| Controller | Bounded speed/turning, inflated footprint, swept collision checks |
| Sensors/perception | RGB/depth, local heat/smoke, timestamp-aligned person evidence |
| Belief state | Observed hazards, explored space, deduplicated potential survivors |
| Mission agent | Inspect, request routes, navigate, escort, retreat/stop, report unresolved |
| Planner | Distance-only baseline or risk/uncertainty-weighted A*, route revalidation |
| Operator UI/evaluator | Edit scenarios, render truth/belief views, record events and paired metrics |

The simulator owns time and outcomes. Browser-rendered camera frames must be matched
to simulation pose/timestamp; reject stale frames. Camera processing delays must not
block controller updates. Initial targets: controller 20 Hz, hazards and route checks
5 Hz, camera/detection 2–5 Hz, viewer 30 FPS; measure rather than promise these rates.

## Truth, sensing, and action boundaries

Simulator truth is privileged: full hazard grids, scenario survivor locations, ignition
points, and future deterministic evolution. Operator visualization and evaluation may
read it. The agent may read static scanned geometry, simulator-provided localization,
and local sensor observations only. Use separate serializers/endpoints for truth and
agent data; hiding fields visually is insufficient. Do not pass scenario objects to
the reasoning context. Test this boundary at API/tool level.

RGB-D has limited range/FOV, wall occlusion, smoke attenuation, and depth invalidation.
Heat/smoke sensors measure local normalized values. Detection boxes/confidence originate
from RGB inference, then synchronized depth/pose estimate world locations. Confirm
across frames and spatially deduplicate. Detection indicates a potential survivor,
not identity or medical state. Synthetic/oracle test detections are labeled fixtures.

Reason on events: survivor confirmed, inspection complete, route invalidated, action
failed. Tool results update subsequent decisions. Validate action name/arguments,
observation version, destination, and current route feasibility; reject stale results.
Record concise evidence and action rationale, not private chain-of-thought. On timeout
or malformed response, stop safely or follow a still-validated retreat route; expose
degraded operation. The model cannot override collision or hazard constraints.

Risk-aware A* includes distance, observed heat/smoke, uncertainty, and conservative
near-term risk inferred from observed growth. It cannot inspect future truth. Inflate
obstacles for robot and follower clearance; apply separate hazard limits. Escort needs
a sufficiently observed route. Unknown space can be inspected cautiously. Validate the
follower position and exposure throughout movement; report unresolved when no viable
route exists. A single survivor follows at a time.

## Shared interfaces

Use metres, seconds of simulation time, and Z-up internally. Version one explicit
viewer transform; grid metadata includes origin, resolution, dimensions, and axis
convention. Define shared serialized types before parallel implementation:

| Contract | Required content |
|---|---|
| SceneBundle | Scene/source IDs, geometry references, rooms, occupancy, exits, transform, CoStar context/provenance |
| Scenario | Scene ID, seed, robot start, survivor/ignition placements, simulation configuration |
| HazardFrame (privileged) | Timestamp, grid metadata, heat/smoke/fuel arrays |
| Observation | Timestamp/version, pose, camera metadata, RGB/depth references, local readings, detection evidence |
| MissionAction | Action ID, observation version, tool name, arguments |
| MissionEvent | Timestamp, action/result, evidence references, readable explanation |

Provide REST scene/scenario/run controls and separate WebSocket visualization and
observation streams. Reset clears belief, detections, pending actions, metrics, and
hazards; pause freezes simulation time. Pin scenario/configuration and planner variant
for reports. Persist seed/configuration and event history; model nondeterminism means
a seed alone does not reproduce reasoning decisions.

## Stack decisions and extension points

| Technology | Decision |
|---|---|
| Terra Incognita / Isaac Sim | Defer quadruped dynamics and scene-conversion overhead |
| TARE | Borrow coverage ideas; no ROS 1 dependency |
| FAR | Optional two-hour post-baseline experiment behind planner interface |
| Elevation Mapping CuPy | Defer; no terrain/debris requirement |
| RTAB-Map / BIEVR-LIO | Defer SLAM; known geometry and perfect localization |
| Nav2 | Future ROS execution/recovery integration |

Keep A* available if FAR fails integration or loses hazard/follower checks. Reconfirm
upstream compatibility before attempting ROS integration; an existing ROS installation
is not an MVP prerequisite. Vexcel exterior planning and full physical simulation are
future work. The hazard engine is illustrative, not calibrated fire prediction.
