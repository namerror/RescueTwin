# Data integration: implementation and evidence

Updated 2026-11-14. Source assets, generated bundles, captures and raw reports live in
ignored `data/`; this document distinguishes implementation from sponsor-data success.
For the latest reviewed-tree checks and repeatable demo, see [baseline rehearsal](BASELINE_REHEARSAL.md).

## Verified asset findings

The earlier conclusion “MatterPak unavailable, therefore no mesh” was too broad.
The Model API's `assets.meshes` and `assets.textures` return assets independently of
`assets.resources`. Reference: [Matterport's mesh query](https://www.postman.com/matterport-developer-support/model-api-snippets/request/vysfcxr/get-meshes).
The [Showcase Bundle SDK](https://matterport.github.io/developer-docs/bundle-sdk/) is a hosted
Showcase integration, not the selected route for this application's own renderer.

Fourteen commercial-linked, single-floor candidates with 4–20 indexed rooms and
`splat=yes` were inspected and their two OBJ variants downloaded. Each model exposes
two mesh records: a full-footprint `50k` OBJ without UVs and a partial `500k` chunk
(OBJ chunks name `lod2_room0_chunk262145.obj`). The chunk references
`lod2_room0_chunk4096.mtl`; its linked URL returns HTTP 403 for both shortlisted
scenes. No endpoint or manifest exposed additional chunks in the response we inspected.
The 50k texture atlases exist, but cannot repair a missing UV/material mapping.
This is a bounded candidate survey, not a claim about every sponsor model.
Evidence: `data/validation/asset-survey.json` and per-model `source-assets/inspection.json`.

| Scene | Full-footprint OBJ | Textured response | Geometry import experiment |
|---|---|---|---|
| `9A46aT7FCPg` | 11,765 vertices, 4,973 triangles, no UVs | 4,421 vertices; partial x extent 9.75–13.61 m versus floor −1.12–18.88 m; missing referenced MTL | About 97.9% floor coverage; conservative collision projection disconnects required points; rejected |
| `5p6KKvHqh3W` | 5,844 vertices, 1,948 triangles, no UVs | 331 vertices; partial floor extent; missing referenced MTL | About 97.3% floor coverage; conservative collision projection disconnects required points; rejected |

The available 50k JPEG atlas sets cannot be mapped to a UV-less mesh merely by
matching model IDs. No generated UVs, guessed atlas assignments, or partial chunks
are presented as original textured imagery. Both runnable sponsor scenes therefore
retain the explicitly labeled DXF-derived mesh and photogrammetric floor image.

**Remaining source-asset gate:** obtain a complete matching OBJ + MTL + texture export
(or documented mapping/complete chunk manifest), then reconcile scan obstacles/closed
doors with the grid and verified exits. The collision check is conservative, not a
claim that every reported obstruction is physically impassable. Do not relax it
without documented registration or portal evidence. No purchases or sponsor messages
were made.

## Reusable workflow

Use the existing Python environment with `.[test,scene,vision]` installed:

```sh
.venv/bin/python scripts/scene_assets.py inspect MODEL --fetch
.venv/bin/python scripts/prepare_scene.py fetch MODEL
.venv/bin/python scripts/prepare_scene.py build MODEL
.venv/bin/python scripts/scene_assets.py import MODEL /path/to/complete.obj
# Or a ZIP containing one complete OBJ and its relative material dependencies.
.venv/bin/python scripts/scene_assets.py import MODEL /path/to/export.zip
```

Configure `scenes/MODEL.json`: `model`, `floor_label`, verified `exits`, `property_id`,
`floor_plan_id`, `resolution`, optional `robot_start` grid cell, optional 4×4 row-major
`source_to_model` affine matrix, and `floor_elevation_m` (default 0). Source coordinates
are metres/Z-up unless an explicit evidenced transform says otherwise. Multi-floor
scans are rejected until their assets are scoped to a single floor.

Import validates indices, UVs, MTL dependencies, local paths, floor coverage (≥85%),
wall agreement (≥85% within one grid cell), blocking cells without source floor coverage, and connectivity of start/exits/observation
points after conservative obstacle projection. OBJ/MTL assets are flattened to a
manifest allowlist with hashes. Failed geometry imports leave the current bundle
unchanged and write `source-mesh-validation.json`. `--allow-untextured` permits only
an explicitly labeled geometry experiment; it does not waive navigation checks.
Only basic `map_Kd` material maps are supported; unsupported map options fail clearly.

Bundles retain `rescuetwin.scene/1` compatibility. New optional mesh fields are
`format`, `materials`, `textured`, `frame`, `transform`, and `validation`; `assets`
contains allowed dependency filenames and SHA256 hashes. Existing GLTF bundles load
unchanged. The API serves only declared assets and rejects traversal/symlink escapes.

Select a prepared scene in the UI, or set `RESCUETWIN_SCENE=MODEL-floor1` before
backend startup. The default remains `9A46aT7FCPg` when present. The frontend supports
`RESCUETWIN_API_URL` for an isolated backend; default is `http://127.0.0.1:8000`.

Both views share Three.js geometry/materials and `(x,y,z) → (x,z,-y)`. Source registration
is applied once. The operator source-mesh cutaway clips above 1.6 m; robot RGB/depth
use the complete geometry. Source meshes do not receive a substitute floor image or
invented ceiling. Stale loads are discarded and disposed; asset failures disable
mission start in the UI. Synthetic test packages are not evidence of sponsor imagery.

## Why CoStar capacity experiments

Both scenes join commercial CSV PropertyId → commercial-property.id, then exact
Matterport attachment videoKey with enum 36/234 → floor-plan id. Apartment-listing
records corroborate via propertyId and apartmentModels[].id. Provenance retains record
IDs, types, field paths, units and derivation. Conflicting known capacities disable
presets; null and enum 0 remain unknown.

The selected contribution is hypothetical evacuation workload, not live occupancy:
25%, 50%, and 100% of matched capacity (rounded up; duplicate counts removed). Each
preset reports requested/placed counts and truncation. Capacity remains 8 for the
current scene and 5 for the second. Sprinkler/construction fields are unknown for both;
the current property has no tenancy records. Neither supports a measured fire-risk
coefficient or a live survivor count. Rental availability is not occupancy.

```sh
.venv/bin/python -m scripts.capacity_experiments data/prepared/9A46aT7FCPg/bundle --output data/validation/current-capacity.json
.venv/bin/python -m scripts.capacity_experiments data/prepared/5p6KKvHqh3W/bundle --output data/validation/second-capacity.json
```

These headless comparisons use **analytic-development perception**, frozen scenario
hashes, seed 7, identical sensors/hazards/safety rules within each pair, and no hosted
agent. Select the same capacity presets and “Compare strategies” in the browser for
image-mode paired runs. Never label headless results as YOLO or LLM performance.

## Vexcel comparison and access

`property/extract` returned HTTP 200 for both scan-index coordinates, with 7 and 12
candidate features respectively. No first-feature parcel was automatically selected.
Reproduce with `scripts/vexcel_probe.py MODEL --output data/validation/vexcel-MODEL.json`.
Credentials stay server-side; output records coordinate source, SRID, units, timestamp,
endpoint, candidate geometry/attributes and the unresolved registration.

| Layer | Useful contribution | Access | Remaining effort |
|---|---|---|---|
| CoStar kit | Matched unit capacity drives reproducible evacuation-load experiments | Local documented JSONL + schemas; no runtime API | Implemented joins/presets/reports; no live occupancy |
| Vexcel | Parcel/structure and roof context; ortho/oblique imagery for responder approach planning | API key and geographic/product coverage; parcel probe succeeded | Verify parcel, capture dates and building; register local frame using geographic control points/orientation/elevation; validate exterior routes |

The supplied Vexcel quickstart documents ortho/oblique extracts, property attributes,
custom detections and embedding search. These are exterior observations, not interior
fuel or survivor detections. A latitude/longitude point cannot establish indoor yaw,
scale, floor altitude or safe egress. Exterior routing remains outside this one-floor
MVP; CoStar is the implemented complementary contribution.

## Capacity experiment results (seed 7, analytic-development)

| Scene / hypothetical people | Baseline evacuated / seconds | Risk-aware evacuated / seconds |
|---|---|---|
| Current / 2 | 2 / 195.2 | 2 / 203.2 |
| Current / 4 | 4 / 398.2 | 4 / 406.2 |
| Current / 8 | 8 / 578.2 | 8 / 588.2 |
| Second / 2 | 2 / 238.4 | 2 / 238.4 |
| Second / 3 | 2 / 232.2 (unresolved) | 2 / 240.2 (unresolved) |
| Second / 5 | 5 / 337.6 | 5 / 339.6 |

These cases do not demonstrate a consistent adaptive advantage. Current-scene exposure
was slightly higher for the adaptive policy at every tested load. Counts change the
illustrative placements and ignition selection between loads, so these are distinct
scenarios, not an isolated causal estimate of the effect of occupancy. Within each
baseline/adaptive pair all scenario inputs are identical. Reports preserve complete
scenarios, hashes, metrics and capacity provenance for replay.

## Verification and remaining gates

- Backend suite: 46 passed, 1 opt-in YOLO fixture test skipped at the last full run;
  The opt-in skip is not evidence of a successful image-mode mission.
- TypeScript checking and Vite production build passed; Vite retains its bundle-size warning.
- Browser: both sponsor scenes loaded, switched, and exposed standard plus three
  capacity presets with no JavaScript page errors. These remain derived geometry.
- A clearly labeled synthetic OBJ/MTL fixture rendered green texture in both views:
  74.7% green pixels in robot RGB, 146,766 valid depth pixels, and 2.75 m maximum
  depth matching the camera-to-wall distance. Operator geometry toggling left depth
  unchanged. A deliberately missing MTL kept mission start disabled. This validates
  loader/rendering/error behavior, not source-backed sponsor imagery.
- The current-scene image-mode mission acceptance did **not** pass: after a 180-second
  wall-clock budget (about 123 simulated seconds under software rendering), the mission
  was still running with zero detections/evacuations and no degraded flag. Early RGB
  captures show a partially wall-occluded, side-on synthetic avatar; later frames show
  room surfaces. Visibility/placement and detector behavior need further investigation.
  This is not a completed YOLO mission or proof of a regression from the prior build.
- Headless paired capacity experiments completed on both scenes; their reported
  completed/unresolved outcomes are preserved above, including failures.
- Vexcel parcel access works; parcel selection, imagery alignment and exterior routing
  remain unverified. Complete Matterport textured assets and collision reconciliation
  remain the source-geometry gates. The original app on ports 8000/5173 was not restarted.

Validation commands (isolated server, e2e extra and Chromium required):

```sh
.venv/bin/python scripts/verify_integration_ui.py --url http://127.0.0.1:5174
.venv/bin/python scripts/verify_material_render.py
```

The first command deliberately exits nonzero if the rendered mission does not complete;
it saves intermediate RGB captures and a report instead of reporting a partial pass.
`data/validation/browser/` contains the full-budget run, `browser-diagnostic/` the early
frames, and `material-fixture/` the synthetic material evidence. Neither test sends a
message, deploys the application, or mutates the existing server instance through its API.

The source-mesh wall alignment scores were 99.57% and 99.67%; the default entry/start
cells intersect the conservative source obstacle projection in both scenes. That is a
portal/clearance reconciliation gate, not evidence that the whole scan is misregistered.
Browser paired reports also carry the frozen scenario hash, placed count, marketed
capacity and capacity field provenance; these remain operator/evaluation data.


## Follow-up after the sponsor-data review

The updated browser flow completes the clear-view rendered YOLO mission and then applies
CoStar's 25% capacity preset, runs the distance-only/risk-aware comparison, and verifies
the report displays capacity provenance and the frozen scenario hash. Reproduce with
`python scripts/verify_integration_ui.py --url http://127.0.0.1:5174 --timeout 180`.
The first and latest reports are in ignored `data/validation/browser-clear-view/` and
`browser-clear-view-costar/` respectively.

For the sponsor meeting, request one complete export bundle for either chosen scan:
OBJ, matching MTL, every referenced texture at the same quality/resolution, and either
a single complete mesh or an explicit complete chunk list with coordinate frame,
registration, and material dependency map. Ask why the `lod2_room0_chunk4096.mtl` URL
returns 403 when the API supplies a downloadable OBJ that references it. If the
MatterPak is expected to provide the bundle, ask whether its unavailable status is an
account permission, export-generation, or model-asset limitation. No purchase or
external request has been made.
