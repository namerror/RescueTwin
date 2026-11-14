# Current runtime contracts

These describe implemented interfaces. The richer contracts in ARCHITECTURE.md remain targets.

## Coordinates and clock

Grid cells are integer `[column,row]`, with 0.5 metres per cell in the synthetic fixture.
The renderer converts these to world-space positions. Current routing and execution use
four-connected grid steps, at one cell/second; there is no rotational or acceleration model.
Time advances by 0.2 simulated seconds per tick. Hazard fields update on that same clock.
No physics teammate implementation existed in the repository when this fixture was added;
`Simulation.hazards(dt)` is the replacement boundary for their work.

## HTTP and streams

- `GET /api/scenes`: available scene metadata (currently synthetic only).
- `GET /api/state`: operator truth, scenario, robot, people, fields, route, events, metrics.
- `GET /api/observations`: robot-local belief and confirmed people; excludes scenario inputs.
- `POST /api/scenario`: `{survivors: [[x,y]], ignitions: [[x,y]], seed: integer}`. Idle only.
- `POST /api/control`: `{action: "start"|"pause"|"reset", mode?: "adaptive"|"baseline"}`.
- `POST /api/compare`: freezes a snapshot and returns `{seed,reports:{baseline,adaptive},note}`.
- `WS /api/stream`: operator-state snapshots, approximately 5 Hz.

Scenario JSON save/load is browser-local; there is no server persistence. Invalid placements
produce HTTP 422; conflicting run state produces 409. Resets preserve current scenario inputs.
The server is for a single local operator, with one active live run. Comparison runs use
independent copied state and do not replace it. No authentication or public hosting is supplied.

## Observation boundary

The fixture adapter supplies analytic, wall-occluded, omnidirectional visibility within
seven cells, with smoke filtering. Heat/smoke are measured at the robot; the immediate
movement interlock samples the next cell to stop unsafe entry. This is a simplified proximity
sensor assumption, not a thermal camera. Belief fields currently retain observations without
forecasting or aging. Escort paths require observed cells, but freshness remains future work.

`PersonDetector.detect` is a separate optional real-image adapter; it does not feed the
runtime yet. It returns confidence/boxes and optional camera-space depth coordinates without
simulator entity IDs. Camera rendering, synchronization, world transforms, cross-frame
confirmation, and replacing analytic detections must land together before image-perception claims.

## Hosted mission policy

With `GEMINI_API_KEY` and the optional `agent` extra, the server periodically requests
`select_goal(kind,id,reason)` using only copied observations and static room/exit metadata.
`apply_decision` validates visibility of survivor identities, destination IDs, freshness,
active escort ownership, and route availability. Calls time out after eight seconds; failures
pause the run. A reset invalidates outstanding results. The current periodic scheduler is a
baseline; event-triggered scheduling is still a target. Without credentials, deterministic
room/survivor selection is active. A* always owns route execution.

## Metrics

`distance`: robot travel in metres. `exposure`: sum over non-evacuated simulated survivors
of `(normalized heat + normalized smoke) * dt`, including exposure before discovery.
`evacuated`: number delivered to floor-level endpoints. Evaluation may use truth; the agent may not.
These units have no calibrated medical/fire-safety interpretation. The deterministic illustrative
hazard model currently has no stochastic variation, so changing a seed alone does not alter it.
