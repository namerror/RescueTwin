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

This repository runs a search-and-evacuation rehearsal on one scanned sponsor property: Floor 1 of
Matterport model `9A46aT7FCPg` (1276 W 38th St, unit 2), joined to its CoStar record and floor plan.
Real: the property record and scan-derived floor geometry. Synthetic: fire, smoke, people, robot. The unit
has one verified exit (see `docs/SCENE_SELECTION.md`). Survivors are found by YOLO on robot-camera frames
rendered in the browser; an analytic fixture detector remains for CI and the synthetic development scene.
Hosted Gemini decisions are optional; without credentials the mission uses a deterministic policy.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite localhost URL. Place survivors and ignition points, run either policy, or compare both on identical starting conditions. Exposure is in illustrative units. Use reset before editing. The operator view contains ground truth; `/api/observations` contains robot knowledge only. Keep this development server on localhost; it has no authentication.

```bash
python3 -m pytest -q
python3 scripts/inspect_sponsor.py 9A46aT7FCPg --property 22572250 --plan 6106437
python3 scripts/prepare_scene.py fetch 9A46aT7FCPg   # needs Matterport credentials
python3 scripts/prepare_scene.py build 9A46aT7FCPg
```

The sponsor kit is ignored by Git at `data/costar-hackathon-kit`. Prepared bundles stay in ignored `data/prepared/`. Exits come only from manual annotations in `scenes/`. Keep the browser tab open during image-mode runs, because it renders the robot camera.

Optional hosted reasoning: install `pip install -e '.[agent]'`, export `GEMINI_API_KEY` and optionally `GEMINI_MODEL`, then restart the backend. API access/model availability must be verified against your account. Failed or rejected decisions pause the run. For image perception install `pip install -e '.[vision]'`; YOLO weights download on first use. The survivor model is CesiumMan (CC BY 4.0, Cesium).

See [agent setup](AGENTS.md), [architecture](docs/ARCHITECTURE.md), [MVP](docs/MVP.md), and [implementation guide](docs/IMPLEMENTATION.md).

Implemented payloads and current fidelity limits are documented in [runtime contracts](docs/CONTRACTS.md).
