# Current runtime contracts

These describe implemented interfaces. The richer contracts in ARCHITECTURE.md remain targets.

## Frames, units and clock

Model frame: Matterport model coordinates, metres, Z up. Grid cells are integer `[column,row]`;
cell centre = `origin + (col + 0.5, row + 0.5) * resolution`, rows increase with +y, 4-connected.
The scanned scene uses 0.25 m cells; the synthetic fixture uses 0.5 m with origin (0, 0).
Viewer transform (three.js and the glTF mesh): `(X, Y, Z) = (x, z, -y)`.

Time advances 0.2 simulated seconds per tick. Constants in `rescuetwin/core.py` are metric:
robot speed 0.5 m/s, analytic sensing range 3.5 m, camera range 4 m, horizontal FOV 90°,
camera height 1.0 m, 90°/s rotate-in-place scan at room observation points (image mode only).

## Scene bundle (`rescuetwin.scene/1`)

Built by `python scripts/prepare_scene.py build <model>` into ignored `data/prepared/<model>/bundle/`:

- `scene.json`: `id`, `name`, `source`, `frame`, `grid {origin, resolution, width, height}`,
  `blocked {wall, exterior, void, unreachable}` cell lists, `exits[]` (cell, position, opening width,
  evidence, what lies beyond), `not_exits`, `rooms[]` (Matterport room id, name, label, polygon,
  cells, `observe` cell at a pano capture position), `robot_start`, `time_limit`, `mesh`,
  `floor_image`, `costar` (field-level provenance), `checks`, `provenance`, `assumptions`.
- `mesh.gltf`: derived walls in the viewer frame. `floor.png`: Matterport photogrammetric plan
  with its own origin and 100 px/m.
- Exits come only from `scenes/<model>.json` annotations. The builder refuses an exit whose
  measured schematic opening is under 0.6 m. Builder checks must pass (see SCENE_SELECTION.md).

`rescuetwin/scenes.py` loads every valid bundle plus the fixture. `RESCUETWIN_SCENE` selects the
startup scene (default: first sponsor bundle, else fixture); `RESCUETWIN_DATA` overrides the folder.

## HTTP and streams

- `GET /api/scenes`: summaries. `GET /api/scene`: full current scene. `POST /api/scene {id}`: switch (not while running).
- `GET /api/scenes/{id}/files/{mesh.gltf|floor.png|preview.png}`: bundle files only.
- `GET /api/presets?seed=`: `standard` and `capacity-stress` (or an `unavailable` entry).
- `GET /api/state`: operator truth (scenario, survivors, true heat/smoke, detections, frame request, paired status).
  The scene itself is no longer embedded; clients fetch `/api/scene` when `scene_id` changes.
- `GET /api/observations`: robot belief only: belief heat/smoke, observed cells, confirmed detections (`det-N`).
- `POST /api/scenario {survivors, ignitions, seed, scene_id?}`: idle only; a mismatched `scene_id` is 422.
- `POST /api/control {action: start|pause|reset|toggle_hazards, mode?: adaptive|baseline, perception?: image-yolo|analytic-development}`.
  `toggle_hazards` (upstream PR #1) runs or pauses the fire without the robot (idle becomes paused); it is 409 while
  the mission runs or after 600 s of hazard time. `start` sets and `pause` clears `hazards_running`; scenario edits
  and scene switches are 409 while previewing. State carries `hazard_time` and `hazards_running`.
- `POST /api/compare {perception?}`: analytic → synchronous paired report; image → starts live
  paired runs (baseline then adaptive) and returns status; `GET /api/compare` polls it.
- `POST /api/frames`: rendered RGB-D frame (below). `GET /api/perception`: detector status.
- `WS /api/stream`: operator-state snapshots, approximately 5 Hz.

## Rendered RGB-D frame contract

In image mode the simulator sets `frame_request {seq, time, pose {x, y, z, yaw}}` every 0.4 s of
simulated time and does not advance until that frame is processed (lockstep), or until
`RESCUETWIN_FRAME_TIMEOUT` (10 s wall clock) passes, after which the run continues visibly
`degraded` with no new detections. The browser renders the scene at exactly that pose and posts
`{seq, width, height, rgb (JPEG/PNG data URL), depth (base64 little-endian float32, row 0 = top,
metres along the camera forward axis, 0 = invalid), intrinsics [fx, fy, cx, cy], pose}`.
Frames with a different `seq` or pose are rejected with 409.

The backend runs YOLO (`YOLO_WEIGHTS`, default `yolo26n.pt`, MPS/CUDA/CPU), takes the 40th-percentile
depth in the box centre, back-projects with the intrinsics, and transforms by pose into the model
frame. Evidence within 0.75 m merges into one detection; two observations confirm it. Detection IDs
are perception-assigned (`det-N`). Contact (escort start) is decided simulator-side when the robot is
within 0.75 m of a confirmed detection; no person there retires the detection as `not-found`.

The renderer applies exponential fog from the robot-cell smoke density to RGB only. Survivors are
rendered as CesiumMan (CC BY 4.0, see `frontend/public/models/ATTRIBUTION.md`); primitive
mannequins were not recognised by YOLO26 n–x and remain only as a fallback.

`analytic-development` perception is a labelled fixture: it emits evidence at visible proxy
positions using simulator truth. It is used by CI and the synthetic scene, not for image claims.

## Hazard model (upstream PR #1, illustrative)

The fire model is the one merged in upstream PR #1 (`floor2-hazard-preview`), ported to NumPy. Each
step: ignition cells are held at heat >= 0.9 for the first 45 s of `hazard_time`;
`burn = max(0, heat - 0.14) * fuel * 0.85`; `fuel -= burn * dt * 0.2`; heat changes by
`kh * laplacian + burn - 0.025 * heat` (clamped to [source, 1]); smoke by
`ks * laplacian + 0.6 * burn - 0.015 * smoke` (clamped to [0, 1]). Upstream's per-cell coefficients
(0.3 heat, 0.5 smoke at 0.5 m cells) are rescaled by `(0.5 / resolution)^2`, so behaviour in metres is the
same on any grid and identical to upstream at 0.5 m (`test_upstream_fire_model_identical_on_half_metre_grid`).
Ignition covers cells within 0.3 m of the placed cell. Diffusion only crosses 4-connected free cells, so
walls block and doorways connect. The fire flashes over, then burns out: on the scanned scene about
100 m² is involved by 2 minutes and heat has decayed by about 3 minutes. Deterministic: the seed does not
change hazards. `Simulation.hazards(dt)` remains the physics-teammate boundary.

Sensing: smoke density is visible along the line of sight within range; flames are visible where heat
>= 0.3 (the upstream rendering threshold); other heat is measured only at the robot cell and by the
movement interlock on the next cell. Dense smoke (> 0.5) hides a cell's contents, except the robot's own
cell, which it always measures. This is analytic, not image-based.

Because the fire burns out, a mission whose remaining targets are unreachable now **holds position**
(logged once) instead of ending, and resumes when a route opens. It is reported `unresolved` only when
nothing is left to search or at the time limit. Belief hazard readings older than 60 s keep their risk
penalty but are capped just below the escort hard limits, so a burnt-out area can be re-approached; the
immediate interlock still reads the true next cell before every move.

## Observation boundary and policy

Observations exclude scenario inputs and simulator person IDs. The hosted agent receives a
summary (no full grids) and may select rooms by id, confirmed detections by `det-N`, or exits.
A* always owns execution. Escort requires observed cells with heat < 0.6 and smoke < 0.5.
A mission with no viable route is reported `unresolved`; unseen occupants are not confirmed absent.

## Metrics

`distance` (m), `exposure` (sum over non-evacuated simulated survivors of `(heat + smoke) * dt`,
including before discovery), `evacuated`, `detections` (confirmed), `false_detections`,
`route_changes`, `rooms_inspected`. Units have no medical or fire-safety interpretation.
