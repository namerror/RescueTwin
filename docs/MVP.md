# RescueTwin hackathon MVP

## Outcome and scope

Target: turn an existing property scan and matched CoStar record into an editable
search-and-evacuation rehearsal. Priority order: risk detection, potential-survivor
identification, smart path planning. This is an acceptance specification; application
completion and sponsor integration have not been established by these documents.

One floor, one kinematic robot, two survivors by default, multiple configurable
ignitions, evolving fire/smoke, and inspected floor-level egress endpoints.
Evacuation ends at the floor boundary; downstream corridors/stairs/outdoors are
assumed available. A balcony/window is not automatically an exit. Never invent an
exit. Two endpoints were the original target; neither shortlisted scan has them, so
the project owner accepted `9A46aT7FCPg` with one verified door (see
[scene selection](SCENE_SELECTION.md)). Results on it must state the single-egress limit.

Exclude structural damage, carrying, manipulation, human biomechanics, locomotion
training, SLAM, and exterior navigation. Escort is a simple single-survivor follower.

## Operator workflow

1. Select a prepared scene with source provenance and verified navigation geometry.
2. Place/move/remove survivors on traversable cells; place/remove ignitions on eligible
   fuel cells. Reject wall/out-of-bounds/invalid placements and overlapping entities.
3. Select standard or matched CoStar capacity-stress preset, edit counts, set seed,
   and save/load the scenario. Capacity is context, not actual occupancy.
4. Start, pause, reset, or launch a comparison. Freeze scenario inputs during runs;
   editing requires reset or a new run.
5. Watch exploration, image-based detections, hazard observations, route changes,
   escort, and unresolved outcomes. Toggle operator truth versus robot knowledge.
6. Review/export paired metrics and action/evidence timeline.

Show a cutaway/floor plan, RGB feed with detection overlays, observed hazards,
explored rooms, route, survivor status, and concise decision reasons. Distinguish
not observed, confirmed potential survivor, escorted, evacuated, and unresolved.
Never claim an unseen room is empty. Flag synthetic assets and degraded reasoning.

## Sponsor use and data status

CoStar track is primary; Amazon practical agentic AI is secondary. The local rules
require at least two sponsor layers and consequential agent action. Use Matterport
geometry and CoStar matched floor-plan capacity to drive presets and report context;
Vexcel is deferred. A decorative property card alone does not meet the intended use.
See [local rules](HACKATHON_RULES.md) for criteria and weights.

Candidate `5p6KKvHqh3W` joins PropertyId 4025799 and floor-plan id 2669568 with
`maxOccupants: 5`. Candidate `9A46aT7FCPg` joins PropertyId 22572250 and floor-plan id 6106437
with `maxOccupants: 8`, matched through its nested model/unit attachment. Both property records have unknown (0)
construction material and sprinkler type. Do not infer no sprinklers. Geometry access,
walkable topology, scene alignment, and two exits still require validation; a one-floor
scan does not prove ground-floor access. Prefer the more complex layout only after
these gates pass.

Real: sponsor property records and scans when actually ingested. Derived: occupancy
grid, room/exit annotations, camera transforms. Synthetic: fire/smoke/fuel, survivors,
robot, and rescue outcomes. A synthetic development apartment is acceptable as a
labeled fixture, not evidence of completed sponsor-data integration. Preserve unknown
fields and source lineage. No actual fire, occupancy, or extraction claim follows
from a hypothetical run.

## Comparison and acceptance

Compare distance-only A* with risk/uncertainty-aware A* using identical initial
scenario, seed, sensors, timing settings, mission machinery, and immediate safety
stops. Hazard evolution is the same at equal simulation times; agent decisions need
not be deterministic. Record model/version and configuration. Report evacuated and
unresolved survivors, evacuation time, simulated exposure, route changes, and rooms
inspected, including failures. Do not attribute routing gains specifically to the LLM
without an independent ablation. These are illustrative system results, not real
fire-safety validation or guarantees of safe evacuation.

Acceptance requires:

- Scenario edits affect missions; save/load and reset preserve intended configuration.
- Walls block movement, sensing, and direct hazard transfer; doorways connect fields.
- Agent APIs/tools never expose hidden placements or full live/future hazard truth.
- Visible avatars produce actual image detections; occluded avatars remain unknown.
- RGB, depth, pose, and detections are synchronized; duplicates are consolidated.
- Newly observed danger invalidates/replans a route before unsafe traversal.
- Follower clearance, exposure, and route viability are checked during escort.
- Unreachable survivors produce explicit unresolved outcomes; model failures degrade safely.
- Same seeds reproduce initial conditions/hazards, with honest reasoning-replay limits.
- Paired runs show comparable metrics and the chosen demo survives repeated rehearsals.
