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
  rule and the local interlock always apply. Per docs/ROBOT_CONSTITUTION.md they are never relaxed. A cornered
  escort holds with the person, logs one assistance request and keeps replanning. (An earlier 15 s
  last-resort escort was removed.)
- **Metrics:** `robot_hazard_s` is the time the robot spent in fire or dense smoke. `assistance_requests`
  counts cornered-escort assistance requests. Survivor exposure is unchanged and still excludes the robot.

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
| Residential | Capacity 50% (4) | **unresolved 3/4** at the 600 s limit, exposure 464.5, 10 assistance requests | 4/4, 590.0 s, exposure 474.8, 16 assistance requests |
| Assembly | Standard (3) | 3/3, 169.2 s, exposure 0.0 | 3/3, 75.0 s, exposure 0.0 |
| Assembly | Capacity 50% (4) | 4/4, 182.2 s, exposure 0.0 | 4/4, 92.0 s, exposure 0.0 |

The building use is the same in both columns of each row, so a difference between columns comes from
routing, not from the prior. Exposure is not comparable with the earlier runs in DATA_INTEGRATION.md, because
pacing and the fire-proof robot both changed. These are illustrative, distinct scenarios, not a demonstrated
adaptive advantage.

Results after the constitution change, which removed the last-resort escort. Before it, both Capacity 50%
residential runs evacuated 4/4 in about 300 s with exposure around 31, because the escorts were taken through
fire. Now escorts wait for human-safe routes. Survivors waiting in or near fire accumulate much more exposure,
and the distance-only run runs out of time. That is the honest cost of never relaxing human limits in this
scenario. An assistance request is logged each time an escort becomes blocked again after moving.

## Gemini supervisor (measured 2026-11-14)

Setup: key in the repo-root `.env` (server-side only), `pip install -e '.[agent]'` (google-genai 2.34.0), and
`GEMINI_MODEL=gemini-3.8-flash`.

**Model availability.** At test time the 3.x Flash models (3.8, 3.7, 3.6, flash-latest) returned 503 "high
demand" or timed out, and `gemini-pro-latest` returned 429 (quota). `gemini-3.5-flash-lite` and
`gemini-3.1-flash-lite` answered in 0.7–2.8 s. Decisions therefore came from the lite fallbacks.

**A first version made missions worse.** It re-asked every 5 s, and Gemini alternated targets: 484 s and
exposure 208 against 230 s and 1.5 for the built-in planner. Two changes fixed this:
- the robot commits to a goal until it is reached;
- the model gets per-room travel cost, likelihood and state.

**Results after the fix.** One run each, seed 7, analytic perception, risk-aware routing, 9A46aT7FCPg.

| Scenario | Built-in planner | Gemini supervisor |
|---|---|---|
| Residential Standard (2 people) | 2/2, 229.8 s, exposure 1.5 | 2/2, 218.0 s, exposure 0.7 (8 calls, all accepted, median 0.82 s) |
| Assembly Standard (3 people) | 3/3, 75.0 s, exposure 0.0 | 3/3, 87.0 s, exposure 0.0 (3 calls, all accepted, median 1.06 s) |

Single runs on one scene, with model output that is not deterministic: no claim that Gemini improves outcomes.
Paired strategy comparisons still exclude the hosted agent by design.

### "Pause clock while Gemini thinks" (decision-quality mode)

The switch sits under "Gemini supervisor" in Run simulation. It is off by default, and `POST /api/agent` with
`{"pause_clock": true}` turns it on.

**When on**
- **Clock:** the live mission clock, robot and fire freeze while the robot waits on Gemini.
- **When Gemini is asked:** as soon as each goal is reached, instead of on the 5 s cycle. Every search choice is
  Gemini's own.
- **Failures:** after a failed or rejected call, the built-in planner steers for 5 s or 1 s respectively, so a
  bad model can never stall the mission.
- **Labelling:** the UI shows "Decision-quality mode … not real-time performance". The event log records it, and
  the run's `inference_pause_s` metric counts the paused seconds.
- **Exclusions:** it never applies to paired comparisons.

**When off (real time)**
- The clock runs during inference and the built-in planner steers until Gemini answers.
- Gemini is asked at most every 5 s.

**Safeguards**
- Goals equal to the robot's own cell are rejected.
- Every accepted decision is followed by at least one mission tick, so a hold can never loop. A live run found
  that deadlock (Gemini kept choosing the room the robot was standing in) and it is now covered by a test.

**Evaluation:** `scripts/agent_eval.py` runs built-in vs Gemini real-time vs Gemini paused on frozen scenarios
and writes `data/validation/agent-eval.json`.
