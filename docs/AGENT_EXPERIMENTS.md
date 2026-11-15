# Recorded experiments and agent supervision

Implemented interfaces use `rescuetwin.run/1` and `rescuetwin.observation/1`. Source scans, local recordings, frames, model responses and reports stay in ignored `data/`.

## Replay

Interactive runs record by default; uncheck **Record next run** before starting to opt out. **Recorded runs → Refresh → Replay** loads a historical scene with play/pause, seek, step, speed, truth/belief views and a raw event inspector. **Live** returns to the active mission. Historical mutation requests are blocked. A separate same-origin sensor page maintains live camera rendering during inspection. Avoid running additional renderer tabs during scored experiments.

`GET /api/runs`, `/api/runs/{id}`, `/timeline?start=0&limit=25`, `/snapshot?seq=0`, `/artifacts/{name}` and `/download` read local artifacts. Download includes the frozen scene, assets, complete state/evidence history, submitted RGB/depth and perception results. Each atomic compressed entry is independently seekable; interrupted runs remain incomplete rather than being called completed. The configured root is `RESCUETWIN_RUNS` (default `data/runs`). Operator truth is explicitly separate from agent observations.

## Evidence and actions

Observation envelopes contain schema, snapshot hash, simulation time, map ID, robot pose, remaining mission time, room coverage, risk provenance, perception tracks, local samples, observed mask, escort count, withdrawal and constraints. Heat/smoke samples contain stable observation IDs, cell, modality, normalized simulated value, observation time and source. Unknown uncertainty is null. No grid-wide truth, ignition list, scenario, marketed population or simulator event log is sent to the agent. Compact prompts aggregate observed hazards by room; full evidence stays available to tools.

The controller's existing arrays/policy are preserved. Modality timestamps distinguish measured smoke from visible flame heat and robot-cell heat. A lack of a heat sample is unknown, not zero degrees. These are analytic simulated hazard sensors, including when people are detected through rendered RGB-D. YOLO remains person-only.

Tools: `get_belief`, `get_evidence(ids)`, `list_actions`, `evaluate_routes(target, escort?, start?)`, `get_mission_recommendation`, `evaluate_plans(candidates)`, `commit_action(action_id,snapshot_id,key,reason?)`, `get_action_status`, `cancel_action(reason)`. Plan candidates reference locally generated action IDs. At most three sequences of at most three actions are evaluated. Numerical tools receive a belief-only planning view, never a Simulation clone. Rollouts assume latest observations persist; they do not predict hidden people or run true physics.

Skills cover room/region inspection, detection verification, hazard recheck, approach/contact, escort, wait, simulated assistance request and withdrawal. Movement remains grid A*. Contact/mobility and human escort thresholds remain controller decisions. Actions have running/success/failed/interrupted status and concise outcomes. Snapshot hashes reject stale commitments; idempotency keys prevent duplicate execution. Unknown IDs/invalid arguments return tool errors. Finite reasoning falls back to the unchanged deterministic controller, with a counter.

## Architectures and timing

`react`, `planner`, and `contingent` share tools and budgets. ReAct gathers evidence and commits a skill. Planner compares the mission A* recommendation with alternatives. Contingent asks which uncertainty could change the decision and retains conditional next steps in structured action memory. This is a bounded uncertainty-aware LLM supervisor, not calibrated POMDP inference or online training. The initial contingent variant relies on model-generated conditional suggestions and fresh evidence each decision, rather than a separate probabilistic branch executor.

Each decision allows three model rounds/eight tool executions. Each mission defaults to 24 model requests; an approved campaign may configure 1–48 through `requests_per_mission`. The campaign hard cap defaults to 144 and may be lowered with `maximum_requests`. The deep-room fire comparison used a 36-request cap for its single agent arm. The mission clock does not advance during synchronous supervisor work, network or quota waits. `reasoning_wall_s` is separate measured elapsed time. Event triggers include room-search changes, track status changes, action completion and a 30-second periodic recheck. Results must be labeled optimistic decision-quality experiments.

## Running deterministic experiments

On isolated backend/frontend servers (never against someone's live edited mission):

```sh
RESCUETWIN_AGENT=0 YOLO_WEIGHTS=data/weights/yolo26n.pt .venv/bin/python -m uvicorn rescuetwin.api:app --port 8027
cd frontend
RESCUETWIN_API_URL=http://127.0.0.1:8027 npm run dev -- --port 5197
```

From repository root:

```sh
.venv/bin/python scripts/experiment_campaign.py --perception analytic-development --output data/experiments/analytic.json
.venv/bin/python scripts/experiment_campaign.py --cases distributed --output data/experiments/yolo.json
.venv/bin/python scripts/verify_replay.py
```

Default scenes: 201 Main Street and 3505 Sage Road. The `pickup` case is the targeted approved-pilot candidate; on Sage Road only one valid near-camera placement exists, so its population is explicitly one. The `nearby` case checks one-person scene viability. Hardware rendering is enabled for the browser harness; forcing SwiftShader made otherwise valid scenes too slow for the campaign window. Two fixed cases distribute two people across reachable observation points, then add a remote ignition. Policies are distance-only greedy, risk-aware greedy and unchanged uncached mission A*. Exact completed configurations are skipped on rerun; changing a policy/scenario requires a new identity. Timeout results are retained, not automatically retried. Analytic runs can execute without a browser. YOLO uses the real browser renderer, local cached weights and synthetic avatars in source scans. Stored screenshots are ancillary; a screenshot timeout does not discard a mission.

Fair comparisons freeze scenario, avatar, hazard pacing, sensor/controller settings and geometry. Evaluate evacuation/casualty accounting first, then normalized exposure/time/distance, with degraded perception, fallbacks and incomplete missions reported separately. Do not reuse one policy's frames for another policy's trajectory.

## Explicit Gemini gate

Inference requires explicit campaign approval. The first campaign was approved by the user on 2026-11-15 and is now closed; see the executed-pilot note below. Server startup and the legacy selector cannot activate Gemini. `POST /api/agent {enabled:true}` returns 403. `POST /api/experiments/run` accepts `agent-react`, `agent-planner`, `agent-contingent` only when an externally approved campaign is configured through `RESCUETWIN_APPROVED_CAMPAIGN`.

After the user says **approve**, create a local campaign JSON containing `approval: "approve"`, pinned `model: "gemini-3.5-flash-lite"`, confirmed positive integer `rpm`, `tpm`, `rpd` from AI Studio (user supplied 15 / 250000 / 500), plus the frozen `architectures` and `scenarios` from the pending proposal, and an agreed `budget_usd`. Do not create this approved file during preparation. Billing is never enabled by the application. Restart the isolated backend with that file, then invoke the campaign script with explicit `--approve --policies agent-react agent-planner agent-contingent --cases pickup` and a separate output path. Use one scene first. The shared provider deadline is 30 wall-clock minutes, including waits; the runner also limits the campaign to 30 minutes.

Provider settings: low thinking, maximum 1024 generated tokens including thinking, no hosted search, one request in flight, no automatic retries/model fallback, durable exact-response cache and request ledger. Quota reservations conservatively use serialized request bytes plus tool/protocol allowance. Missing quota configuration blocks requests. Failed/uncertain requests are not repeated automatically. Model/tool messages retain function metadata/signatures for continuation. Quota/account usage elsewhere in the project cannot be inferred from this application's ledger.

Published pricing checked 2026-11-15: $0.30/M input, $2.50/M output including thinking; supported free-tier calls cost $0. At 144 calls averaging 8,000 input and 1,024 output tokens the billed estimate is $0.714. A $2 agreed ceiling would cover conservative reservations, but is not authorization. Exact project quotas and whether billing is needed must be confirmed before the approval handoff. Sources: https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite and https://ai.google.dev/gemini-api/docs/rate-limits.

Run `.venv/bin/python scripts/agent_preflight.py` for the offline pending proposal; it does not grant approval. Final deterministic evidence is under `data/experiments/`; the campaign proposal remains `approval: pending`. Historical failed software-rendered runs are retained and excluded from scored comparisons.


## Executed first Gemini pilot (2026-11-15)

The approved primary-scene comparison is complete. Final corrected reactive/planner/contingent variants all evacuated two people in 16 simulated seconds (greedy: 24; mission A*: 16). Reactive and planner-backed had no fallbacks; contingent had one. This establishes a tie with mission A*, not a win over it. There were 88 generation requests including diagnostics; billing remained unchanged on the user-confirmed free tier. The complete report, usage ledger, request caches and run references are in `data/experiments/gemini-approved-20261115/REPORT.md` and `campaign-complete.json`.

Live validation corrected native argument typing, function-result matching and the approach-person completion condition while escorting somebody else. The contingent prompt now explicitly names its required evaluate_plans operation. All earlier failed/partial variants remain separate in the report. Campaign elapsed time is anchored to the original ledger start across server restarts. No additional model runs are scheduled.


## Completed deep-room fire comparison

The subsequent approved comparison used one frozen 201 Main Street scenario and exactly three runs: risk-aware greedy, mission A*, and planner-backed Gemini. The people were inside the bedroom and kitchen/living area, 11 m and 18.5 m walking distance from the start; the main-hall fire was 4.37 m geometrically from the robot. See `data/experiments/room-fire-single/DESIGN.md` for the predeclared design and `REPORT.md` for recorded routes, decisions, timing and limitations.

All methods evacuated both people. Recorded times were greedy 176.4 s, mission A* 164.2 s, and agent 185.4 s. The deeper test exposed duplicate inspection scans in the agent adapter (40 s scanning versus A*'s 20 s); that confounds attribution of the time gap to reasoning. The adapter now reuses the controller's completed room scan, with an offline regression test. The experiment was not rerun after the fix; its original execution code and fingerprints are preserved under `as-run-code/`. No corrected performance claim is made.

The comparison used 25 free-tier generation requests, zero API failures and one bounded decision fallback. Its `campaign-complete.json` closes the authorization. The provider rejects completed campaign directories; replay remains read-only and requires no model access. Additional experiments require a new explicit approval.

For an explicitly authorized custom scenario, the existing runner accepts `--scenario-file PATH` together with the selected model and policy list. This loads one frozen case rather than generating the default cases; existing result identities are skipped, and wall-time limits are recorded as incomplete outcomes. Do not rerun the completed fire comparison automatically.
