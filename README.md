# RescueTwin — AI-Powered Emergency Robotics Simulation

**RescueTwin** is an AI-powered robotics simulation platform that transforms real-world building digital twins into interactive environments for autonomous emergency response and search-and-rescue planning.

Using **CoStar property data, Matterport 3D building scans, and Vexcel aerial imagery**, RescueTwin reconstructs real commercial spaces where simulated autonomous robots can navigate, investigate hazards, locate potential victims, and plan safe routes during emergencies such as fires or blocked evacuations.

## Core Concept

Instead of relying on predefined evacuation routes, RescueTwin enables autonomous agents to dynamically reason about their surroundings, adapt to changing conditions, and make decisions under uncertainty.

## High-Level Design

1. **Spatial Environment:** Convert real building data into navigable simulation environments, including indoor layouts and outdoor access points.
2. **Emergency Simulation:** Introduce hypothetical emergency scenarios involving spreading hazards, blocked passages, and trapped occupants.
3. **Autonomous Robot Agents:** Simulated robots explore the environment, identify potential rescue targets, evaluate risks, and continuously replan their routes as new information becomes available.
4. **AI Reasoning & Planning:** Combine spatial algorithms with agentic AI to coordinate missions, prioritize objectives, and respond to evolving situations.
5. **Interactive Visualization:** Display robot movements, changing environmental conditions, and mission outcomes through an interactive 3D digital twin.

## Vision

**Turn existing building digital twins into intelligent testing grounds for autonomous robots, allowing emergency-response strategies to be explored and evaluated before real-world deployment.**

Beyond emergency response, the underlying platform could support robotic facility inspection, autonomous navigation benchmarking, and pre-deployment planning for commercial buildings.
## Development baseline

This repository defaults to a search-and-evacuation rehearsal on Floor 1 of
Matterport model `9A46aT7FCPg` (1276 W 38th St, unit 2), joined to its CoStar record and floor plan.
All 14 prepared catalog models are selectable as textured simulation scenes.
Real: the property record and scan-derived floor geometry. Synthetic: fire, smoke, people, robot. The unit
has one verified exit (see `docs/SCENE_SELECTION.md`). Survivors are found by YOLO on robot-camera frames
rendered in the browser; an analytic fixture detector remains for CI and the synthetic development scene.
Select **Gemini** under Mission algorithm to enable hosted decisions. Set server-side `GEMINI_API_KEY`
and optionally `GEMINI_MODEL` in `.env`; manual installs also need `pip install -e '.[agent]'`.
Gemini uses robot observations and controller-validated goals, with deterministic fallback on errors.
The mission clock pauses during inference by default (not real-time performance). Baseline and Mission A*
run without hosted calls. Approved campaign experiments remain separate; see `docs/AGENT_EXPERIMENTS.md`.

```bash
./setup.sh
# Another reviewed scene:
RESCUETWIN_MODEL_ID=XXXXXX ./setup.sh
```

Use Python 3 (with venv support), Node 24 with npm (`nvm use` reads `.nvmrc`),
and `curl` on Linux/macOS or WSL. The first run installs locked Python 3.12.3
and Python dependencies with uv 0.11.25 (bootstrapped locally if needed), runs
`npm ci`, prepares missing textured scene assets, and starts both services.
Later runs skip dependency installation. After dependency changes or a damaged
installation, run `./setup.sh --install` once to resynchronize and start.
No virtualenv activation or second terminal is needed. Ctrl+C stops both services;
if either service exits, the launcher stops its sibling. It never kills existing
services to reclaim an occupied port.

Defaults are model `9A46aT7FCPg`, backend `http://127.0.0.1:8000`, and frontend
`http://127.0.0.1:5173`. Configure `RESCUETWIN_MODEL_ID`,
`RESCUETWIN_BACKEND_PORT`, and `RESCUETWIN_FRONTEND_PORT` in root `.env` or as
exported variables; exported values win. `.env` accepts literal `KEY=value`
assignments with optional matching quotes, not shell commands/interpolation.
The frontend proxy and backend scene ID are selected automatically.

For an uncached scene, place the sponsor kit at `data/costar-hackathon-kit` and
configure `MATTERPORT_API_KEY` / `MATTERPORT_API_TOKEN` (or `MP_API_KEY` /
`MP_API_TOKEN`) in root `.env`. Downloads use the supported MatterPak bundle API.
A complete validated cached bundle needs neither sponsor credentials nor the kit.
The launcher checks asset hashes and rebuilds incomplete bundles in staging;
failed preparation leaves the previous bundle intact and stops startup. New model
IDs require reviewed `scenes/MODEL.json` annotations with verified exits.
YOLO weights are downloaded only when absent under `data/weights`; `YOLO_WEIGHTS`
can point to an existing local model. Setup also downloads the pinned survivor GLB into
`frontend/public/models/Survivor.glb`, verifies its SHA-256, and repairs a missing or
corrupt cache before starting services. Direct frontend launches require running
setup first for this asset. Warm launches use cached assets and perform
no dependency synchronization or asset downloads. Gemini is used when configured;
otherwise the existing deterministic mission policy runs. A mission starts only
when the operator requests it in the browser.

To share prepared scenes across devices without Git:

```bash
# Sender: export annotated, validated local sponsor scenes (no installs or downloads).
./setup.sh --export-scenes /path/to/shared-scenes.json

# Recipient: copy that JSON through file sharing, then fetch/rebuild and start.
./setup.sh --import-scenes /path/to/shared-scenes.json
```

Recipients need the CoStar kit at `data/costar-hackathon-kit` and their own
Matterport credentials for uncached assets. The versioned manifest includes model
IDs, complete reviewed annotations (including exit evidence and registration),
CoStar record references, and a default model. It excludes downloaded assets,
credentials, and signed download URLs. Export includes only annotated prepared
scenes and skips scenes without annotations. If none qualify, export reports an
error without writing a manifest. Unannotated models still need reviewed exits
before simulation use.
The configured `RESCUETWIN_MODEL_ID` becomes the manifest default when included;
otherwise export prefers `9A46aT7FCPg`, then the first exported model. Import uses
the manifest default regardless of `RESCUETWIN_MODEL_ID`.

Import validates the entire manifest first, attempts each model, and reports a
summary. It starts services only if every model succeeds. Retry the same command
after fixing a failure; successful scenes are cached. Preparation happens in
staging and failed rebuilds preserve the previous published bundle. Bundles made
before annotation fingerprinting rebuild once to establish their recipe; existing
valid source downloads are reused. Later imports reuse unchanged validated bundles
and rebuild when annotations change.

Imported recipes stay inside ignored `data/prepared/<model>/bundle/scene.json`;
tracked `scenes/` files are untouched. To reopen an imported scene later, run
`RESCUETWIN_MODEL_ID=<model> ./setup.sh`. Re-export preserves the imported recipe.
For these scenes the embedded recipe takes precedence over `scenes/<model>.json`;
to revise it, edit the shared manifest and import it again. Models without an
apartment property/floor-plan pair retain their annotation references and notes;
the existing apartment CoStar property-card join does not apply to them.
Export, import, and `--environment` are mutually exclusive.

Dependency maintainers: update `uv.lock` with uv 0.11.25 and commit changes to
`pyproject.toml`/`uv.lock` together; commit frontend dependency changes with
`package-lock.json`. Teammates then run `./setup.sh --install`. Files under
`data/`, `.venv/`, and `.bootstrap-venv/` remain local and ignored.

Open the Vite localhost URL. Place survivors and ignition points, run either policy, or compare both on identical starting conditions. Exposure is in illustrative units. Use reset before editing. The operator view contains ground truth; `/api/observations` contains robot knowledge only. Keep this development server on localhost; it has no authentication.

For a faster demo, start the app with `RESCUETWIN_RUN_SPEED=2 ./setup.sh`. This targets twice the normal wall-clock pace with the same simulation timestep. Browser-rendered camera frames, inference, and simulation compute can limit the actual pace.

To start directly on a local annotated floor plan, use `./setup.sh --environment /path/to/floor_plan.json`. The run starts with analytic development perception selected. If a local YOLO model exists at `data/weights/yolo26n.pt` or `yolo26n.pt`, setup warms it before opening the viewer. You can also set `YOLO_WEIGHTS` to another local model and select **YOLO · RGB-D** in the viewer before starting a run.

**Browse prepared models** opens all locally downloaded scans in a separate 3D
viewer. All 14 catalog models now have their own textured MatterPak assets and
validated simulation bundles, selectable directly from **Scene**. **Plan** shows the active scene
from above. **Import custom floor plan JSON…** loads an annotated custom map;
it does not open a scan or accept ordinary images/PDFs. See
[prepared models](docs/PREPARED_MODELS.md).

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/inspect_sponsor.py 9A46aT7FCPg --property 22572250 --plan 6106437
.venv/bin/python scripts/prepare_scene.py fetch 9A46aT7FCPg   # needs Matterport credentials
.venv/bin/python scripts/prepare_scene.py build 9A46aT7FCPg
```

The sponsor kit is ignored by Git at `data/costar-hackathon-kit`. Prepared bundles stay in ignored `data/prepared/`. Exits come only from manual annotations in `scenes/`. Keep the browser tab open during image-mode runs, because it renders the robot camera. In Operator truth, survivor labels and blue occlusion silhouettes keep simulated people visible through scan walls; **Find survivor** cycles through close-up views. These aids are hidden from Robot belief and excluded from robot RGB-D.

Optional hosted reasoning (dependencies included by setup): export `GEMINI_API_KEY` and optionally `GEMINI_MODEL`, then restart the backend. API access/model availability must be verified against your account. Failed or rejected decisions pause the run. Image perception dependencies and weights are prepared by setup. The survivor model is a textured Microsoft Rocketbox civilian (MIT), fetched automatically by `./setup.sh`; see `frontend/public/models/ATTRIBUTION.md`.

See [agent setup](AGENTS.md), [architecture](docs/ARCHITECTURE.md), [MVP](docs/MVP.md), and [implementation guide](docs/IMPLEMENTATION.md).

Implemented payloads and current fidelity limits are documented in [runtime contracts](docs/CONTRACTS.md).


## Source asset integration update (2026-11-14)

See [data integration evidence and workflow](docs/DATA_INTEGRATION.md) for direct OBJ imports,
material/geometry gates, the two-scene workflow, capacity experiments, and Vexcel findings.
The [baseline rehearsal](docs/BASELINE_REHEARSAL.md) records current checks, the demo
sequence, paired results and remaining sponsor-data gates.
