# Development and runtime reference

See [Data and setup](DATA.md) for authorized scene inputs and [Architecture](ARCHITECTURE.md) for component boundaries. Private sponsor files are intentionally absent from Git.

## Install and check

`./setup.sh` manages the pinned Python environment and npm installation. After dependency changes use `./setup.sh --install`. For manual installation:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,scene,vision]'
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python -m pytest -q
```

The `agent` extra adds Gemini; `e2e` adds Playwright. Browser checks need a Chromium installation. Setup fetches pinned survivor meshes and missing local YOLO weights; direct frontend launches need those assets prepared first. Third-party model attribution is in [ATTRIBUTION.md](../frontend/public/models/ATTRIBUTION.md).

Offline tests use synthetic geometry and sponsor-record fixtures. Set `RESCUETWIN_TEST_BUNDLE` to a private prepared bundle to opt into its alignment/mission check. Set `RESCUETWIN_TEST_ANNOTATION` to a private apartment annotation to opt into the CoStar provenance check. Sponsor identifiers must never be embedded in tests.

For manual services, select a local scene through `RESCUETWIN_SCENE`, or use the labeled synthetic fixture:

```bash
RESCUETWIN_SCENE=development-apartment .venv/bin/python -m uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8000
# Separate terminal:
cd frontend
npm run dev
```

The registry uses `RESCUETWIN_DATA` (default `data/prepared`). Explicit `RESCUETWIN_SCENE` selects a scene; otherwise the configured `RESCUETWIN_MODEL_ID` is preferred, then the first valid local sponsor scene, then the synthetic fixture. `.env` is read by the launcher; export variables when starting services directly.

Dependency updates must include `uv.lock` or frontend `package-lock.json` as appropriate. Keep artifacts under ignored `data/` or frontend build/cache directories.

## Interfaces

The localhost API has no authentication. FastAPI exposes its generated endpoint schemas at `/docs`.

| Interface | Purpose |
| --- | --- |
| `GET /api/scenes`, `GET/POST /api/scene` | List local scenes, inspect or switch the active scene. |
| `GET /api/scenes/{id}/files/{name}` | Serve declared local bundle assets only. |
| `GET /api/state`, `WS /api/stream` | Operator truth and state updates. Never use as agent observations. |
| `GET /api/observations` | Versioned robot observation/belief envelope. |
| `GET /api/presets`, `POST /api/scenario` | Inspect presets and apply validated scenario placements. |
| `POST /api/control` | Start, pause, reset, or preview hazards; select routing, perception, and mission strategy. |
| `POST /api/frames`, `GET /api/perception` | Submit synchronized rendered RGB-D and inspect detector health. |
| `POST/GET /api/compare` | Run/poll a frozen distance-only versus risk-aware pair. |
| `GET/POST /api/agent` | Interactive Gemini status and inference-pause controls; mission strategy selects activation. |
| `GET /api/runs` and run detail/snapshot/timeline endpoints | Read local recordings and replay evidence. Downloads include private scene data. |
| `POST /api/experiments/run` | Deterministic or separately approved tool-backed campaign execution. |

Scenario editing and scene switches are blocked during active missions/paired runs. Historical replay is read-only. The mission strategies exposed by the workbench are `greedy`, `mission-astar`, and `gemini`; routing is `baseline` (distance-only) or `adaptive` (risk-aware). A cached A* variant is also accepted by the API for development.

## Coordinates, scenes, and rendered frames

- **Model frame:** metres, Z up. Grid cells are integer `[column, row]`; centres are `origin + (cell + 0.5) * resolution`. Navigation is four-connected.
- **Viewer transform:** model `(x, y, z)` maps to Three.js `(x, z, -y)`.
- **Clock:** each tick advances 0.2 simulated seconds. Requested run speed changes wall-clock pacing, not the timestep. Gemini inference pauses are reported separately.
- **`rescuetwin.scene/1`:** a local `scene.json` describes grid, blocked cells, rooms, robot start, reviewed exits, assets, provenance, and validation. OBJ/MTL meshes retain source transforms, material references, and content hashes. The loader rejects invalid bundles and blocked exit/observation cells.
- **RGB-D:** the renderer responds to `frame_request` with sequence, dimensions, RGB data URL, base64 little-endian float32 depth, intrinsics `[fx, fy, cx, cy]`, and pose. Depth is camera-forward metres, top row first; zero is invalid. Sequence/pose mismatches are rejected. Frame timeouts mark perception degraded.

YOLO boxes are projected through depth into the known map. Tracks receive perception IDs, distinct from simulator person IDs. Operator cutaways and survivor visibility aids are excluded from robot RGB-D. Smoke affects rendered visibility; hazard sensing remains analytic. Human meshes and fixture detectors are development/simulation assets.

## Observations, safety, and recording

`rescuetwin.observation/1` includes a snapshot hash, robot pose, remaining time, room coverage, local samples with provenance/timestamps, tracks, and controller constraints. It excludes hidden survivor placements, full hazard truth, and marketed population. Missing uncertainty remains null. Tool commitments validate current snapshot/action IDs; movement remains controller-owned.

Heat and smoke are normalized simulation values. Escort requires observed cells below controller limits (heat 0.6, smoke 0.5). A timeout does not relax them. Fire, sprinkler, gas, and fuel behavior are illustrative; source fixture reports do not establish operational equipment or physical fuel properties. Exposure integrates simulated survivor heat/smoke and has no medical interpretation.

`rescuetwin.run/1` stores frozen scene/scenario data, code/settings fingerprints, evidence, state history, and assets under `RESCUETWIN_RUNS` (default `data/runs`). Incomplete runs remain incomplete. Replay reads artifacts without starting simulation or inference.

Interactive Gemini uses the [robot constitution](ROBOT_CONSTITUTION.md) plus observations and validated goals. Separate `react`, `planner`, and `contingent` supervisors use bounded belief-only tools. Campaign configuration must be explicitly approved, include current account quotas and a budget, and retain request ledgers; completed campaigns remain closed. Do not trigger hosted requests from tests or publication capture.

## Optional browser checks

Run on isolated localhost ports, not against an operator’s edited live mission. Pass private model selections explicitly:

```bash
.venv/bin/python scripts/verify_integration_ui.py --url http://127.0.0.1:5174 --model "$RESCUETWIN_MODEL_ID" --mission-only
.venv/bin/python scripts/verify_textured_scenes.py --url http://127.0.0.1:5174 --models "$RESCUETWIN_MODEL_ID"
.venv/bin/python scripts/verify_replay.py --url http://127.0.0.1:5174
```

Captures/reports remain ignored. Distinguish synthetic fixture checks, scan rendering checks, and actual YOLO mission checks; none establishes real emergency-response safety.
