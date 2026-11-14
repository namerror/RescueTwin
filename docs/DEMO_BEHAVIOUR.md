# Demo behaviour: fire pacing, fire-proof robot, smoke, building-use search

Added 2026-11-14 on branch `scan-mesh-fire-tuning`, on top of the teammate's material fire model and
robot hazard sensor (PR #4). All of this is simulated. None of it is a fire-safety or occupancy claim.

## Fire pacing

`HAZARD_TIME_SCALE` (default 0.6; env `RESCUETWIN_HAZARD_TIME_SCALE`) advances the teammate's hazard model
at that fraction of mission time.
- It is a pure time rescale: the coefficients are unchanged, and a test checks the paced model against the
  model run with proportionally shorter steps.
- Set it to 1.0 to reproduce the teammate's timing exactly.
- Existing hazard tests run at 1.0.

## Fire-proof robot

The robot alone has no heat or smoke limit and no hazard interlock.
- **Routing:** the risk-aware policy still charges heat and smoke, so it detours when a reasonable detour
  exists. When fire is the only way, it takes the path with the least observed fire. The distance-only policy
  takes the shortest path.
- **Speed:** in or entering a cell at heat ≥ 0.3 or smoke ≥ 0.5, it moves at `ROBOT_DASH_SPEED` (1.0 m/s)
  instead of 0.5 m/s. This uses only the robot's local measurement.
- **Escorting a person:** people are not fire-proof, so human limits (heat 0.6, smoke 0.5), the observed-cell
  rule and the local interlock still apply. If no route satisfies them for `ESCORT_LAST_RESORT_S` (15 s), the
  escort takes the least-hazard route to the exit at top speed. This happens under both policies and is
  logged and counted.
- **Metrics:** `robot_hazard_s` is the time the robot spent in fire or dense smoke. `last_resort_escorts`
  counts the last-resort escorts. Survivor exposure is unchanged and still excludes the robot.

## Smoke

- **Display fix:** the scan-mesh cutaway now clips only the scan materials (per-material clipping). Smoke
  layers near the ceiling (~2.0–2.2 m) were previously clipped by the global 1.6 m cutaway and vanished.
- **Robot camera:** the robot camera always renders the full geometry.
- **Sensor:** the teammate's hazard sensor (smoke at the robot, plus heat and distance to an ignition point
  within 1 m) appears in the property panel and in `/api/observations`.

## Building-use search prior

`rescuetwin/occupancy.py` maps a building use to room weights.

| Use | Weighting |
|---|---|
| Residential | Uniform: anyone anywhere, so nearest-first search as before |
| Assembly (yoga/fitness, worship, event hall) | Main room 6× |
| Office | Main room 3× |
| Retail | Main room 4× |
| Restaurant | Main room 4×, kitchen 2× |

- **Main room:** the largest room that isn't a service room (bathroom, closet, etc.).
- **Where the use comes from:** CoStar `propertyType`, or the operator's choice in the UI. Without either,
  the prior is uniform.
- **What the robot does with it:** it inspects the room with the best weight ÷ (travel cost + 3 m) next.
- **Knowledge boundary:** the prior reads only static building information (use, room labels and areas). A
  test checks it is identical regardless of where survivors are placed.
- **Scenarios and presets:** `building_use` is part of the frozen scenario, so paired runs share it. It is
  omitted when unset, which keeps earlier scenario hashes. For non-residential uses, presets place people in
  proportion to the same prior.

**Limitation:** when people are placed from the same prior the robot searches by, faster finds are expected
by construction. That shows the behaviour works; it does not validate the prior.

## Headless results on 9A46aT7FCPg

Defurnished scan, seed 7, analytic-development perception.

| Building use | Preset | Distance-only | Risk-aware |
|---|---|---|---|
| Residential (CoStar Multifamily) | Standard (2) | 2/2, 225.6 s, exposure 2.2 | 2/2, 229.8 s, exposure 1.5 |
| Residential | Capacity 50% (4) | 4/4, 297.8 s, exposure 30.4, 2 last-resort escorts | 4/4, 309.4 s, exposure 31.5, 2 last-resort escorts |
| Assembly | Standard (3) | 3/3, 169.2 s, exposure 0.0 | 3/3, 75.0 s, exposure 0.0 |
| Assembly | Capacity 50% (4) | 4/4, 182.2 s, exposure 0.0 | 4/4, 92.0 s, exposure 0.0 |

The building use is the same in both columns of each row, so a difference between columns comes from
routing, not from the prior. Exposure is not comparable with the earlier runs in DATA_INTEGRATION.md, because
pacing and the fire-proof robot both changed. These are illustrative, distinct scenarios, not a demonstrated
adaptive advantage.
