# Final UI integration — 2026-11-15

Integrated upstream `74f8ab5` into `integration/final-ui`. The pre-merge UI checkpoint
is `af8b4ff`. Upstream mission algorithms, agent contracts and activation gates,
recording/replay, renderer leases, sprinkler/gas changes, scenes and scripts remain.
The current light dashboard, responsive stage, inset camera, Follow/POV modes,
scenario tools and runtime speed controls are retained.

The mission selector replaces the legacy agent checkboxes. Recorded runs use the
current styling below the scene. Live simulation speed is separate from replay
speed; replay disables mission mutations and cannot overwrite the camera with a
live preview. Late historical responses are discarded after returning to Live.

The paced clock keeps a 0.2 s simulated step. Supervisor work and recorder capture
run within the eligible step, with a simulation identity check after awaited work.
Camera waits overlap pacing deadlines. Paired runs retain their existing fast path.
A requested wall-clock speed is a target; rendering/inference can limit throughput.

## Verified on the integrated tree

- Pytest: **154 passed, 1 skipped**. Includes six virtual-clock regressions covering
  all speeds, frame waits, stale simulation replacement, pause and inference hold.
- TypeScript/Vite build passes. Existing bundle-size and TestClient deprecation
  warnings remain.
- Full browser rehearsal: both sponsor scenes loaded; scenario JSON imported/applied;
  clear-view RGB-D/YOLO run completed with 1/1 evacuated, 12 simulated seconds,
  6 m travel, zero exposure, two route changes and no degraded perception.
- Analytic route-change segment and Capacity 25% comparison passed; both paired
  arms completed with 2/2 evacuated. Scenario hash and provenance were visible.
- Synthetic material fixture passed: 74.7% green pixels, 146,766 valid depth pixels,
  2.75 m maximum depth. Operator layers/cutaway preserved depth; missing materials
  blocked mission start. This fixture does not establish sponsor image performance.

Evidence is ignored under `data/validation/final-ui/` and
`data/validation/material-fixture/`. The mission and mobile screenshots were visually inspected.
The clear-view mission establishes that placement, not general detection robustness;
normalized simulated exposure is not real-world safety validation.

- Controls/replay browser check: all live speeds and camera modes, mission A*, pause,
  read-only replay seek/step/play, archive download, return to Live and fullscreen
  passed without page errors or replay mutations. Layouts at 1440, 900 and 390 px
  had no horizontal overflow.

## Reproduction

Use isolated servers and the `test,scene,vision,e2e` extras. Backend on port 8001:

```sh
RESCUETWIN_AGENT=0 RESCUETWIN_RUNS=data/validation/final-ui/runs .venv/bin/python -m uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8001
```

Frontend on port 5174, in a separate terminal from `frontend/`:

```sh
RESCUETWIN_API_URL=http://127.0.0.1:8001 npm run dev -- --port 5174 --strictPort
```

From the repository root, run these browser checks sequentially:

```sh
.venv/bin/python scripts/verify_integration_ui.py --url http://127.0.0.1:5174 --timeout 240 --output data/validation/final-ui/browser
.venv/bin/python scripts/verify_material_render.py
.venv/bin/python scripts/verify_final_ui.py
```

This machine's virtualenv Playwright package was incomplete; verification used an
isolated temporary installation via PYTHONPATH. No agent campaign or hosted model
inference was invoked. No push or deployment was performed.
