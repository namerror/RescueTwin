# Textured MatterPak integration

Updated 2026-11-14. Sponsor downloads, prepared bundles, model weights and captures
remain ignored under `data/` (weights may also reside at the ignored repo root).

## Source correction and implementation

The supported download query is:

```graphql
query($id: ID!) {
  model(id: $id) { bundle(id: "mp:matterpak") { assets { url } } }
}
```

Earlier claims of unavailable MatterPaks were based on
`assets.resources(format: "matterpak")`, a different query. They were incorrect.
Partial `assets.meshes` chunks and UV-less defurnished meshes are not the selected assets.

`scene_assets.py matterpak` range-reads the ZIP, excludes point clouds and floor-plan
exports, validates archive paths/symlinks/size, and publishes a content-addressed
extraction. Its URL-free status records model, query source, members and SHA256 hashes.
Download failures preserve previous extractions and status. No signed URLs are saved.

Import validates the OBJ's face indices, UVs, material assignments and dependencies.
The default package has 34 materials with both `map_Ka` and `map_Kd`. Ambient map
references are validated but omitted from prepared MTLs: Three.js uses the preserved
diffuse maps with unlit, double-sided materials and sRGB textures for baked lighting.
OBJ, rewritten MTLs and images use content-addressed filenames. Assets are staged and
published before atomic manifest replacement; failed imports preserve the previous
manifest and its referenced bytes. Old assets are retained for active clients.

## Commands and scene switching

```sh
.venv/bin/python -m pip install -e '.[test,scene,vision,e2e]'
.venv/bin/python scripts/prepare_scene.py fetch MODEL
.venv/bin/python scripts/prepare_scene.py build MODEL
.venv/bin/python scripts/scene_assets.py matterpak MODEL
.venv/bin/python scripts/scene_assets.py import MODEL PRINTED_OBJ_PATH
```

Run separately for `9A46aT7FCPg` and `5p6KKvHqh3W`. The MatterPak command prints the
local OBJ path. Do not use `--allow-untextured`. Building creates a derived baseline;
import must succeed before declaring the textured scene ready. Restart the backend
after preparing bundles because its scene registry is loaded at startup. Select the
property in the UI; mesh, MTL and texture URLs are scoped by scene and content hash.
No code edits are required for switching between these configured scenes.

Exact imported sources:

```sh
.venv/bin/python scripts/scene_assets.py import 9A46aT7FCPg data/prepared/9A46aT7FCPg/source-assets/matterpak-29fea1969ffda307b8b1/8a07a9b4ad924205a20ca57ec2256111.obj
.venv/bin/python scripts/scene_assets.py import 5p6KKvHqh3W data/prepared/5p6KKvHqh3W/source-assets/matterpak-910cefb12d893130d084/af28274fd2f34ab29f43a2f5a6b471f5.obj
```

Each extraction's `source-assets/matterpak-status.json` contains full hashes.
The supplied `data/mp_matterpak_4BR-3BA-1613SF_9A46aT7FCPg/` is another local default-scene
package; the corrected command independently obtained the complete package.

## Geometry validation

The default OBJ contains 240,471 faces and 34 JPEGs; the second contains 304,370 faces and 20 JPEGs. Both have complete per-face UV/material mappings.

Both scans passed unchanged reconciliation thresholds, with identity source-to-model
registration, metres and Z-up. The viewer applies the existing Z-up to Y-up transform.

| Model | Floor coverage | Wall agreement | Furniture cells | Source wall cells | Portals | Observation moves | Skipped rooms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 9A46aT7FCPg | 95.25% | 99.78% | 299 | 201 | 21 | 0 | 0 |
| 5p6KKvHqh3W | 96.14% | 99.34% | 56 | 111 | 28 | 1 | 0 |

Start and verified exit cells remain connected in both. Full results and individual
portal/relocation records are in each model's `source-mesh-validation.json`.
Furniture remains blocked. Only thin obstructions in schematic portals or verified
exit openings are cleared; doors are assumed openable. Robot-band projection is
conservative and is not a certified traversability model. The robot RGB-D view retains
full scanned doors even where navigation treats them as openable; this is a known
scan-versus-simulation limitation. No furniture fuel-contract changes were made.

The operator and robot camera share the same registered mesh. Cutaway is operator-only;
robot RGB and linear camera-Z depth use full geometry. Neither source geometry nor
rendered textures expose simulator survivor IDs to the agent. Single verified unit
entry exits remain unchanged; balconies are not exits. Beyond-door egress is assumed.

## Verification

- Backend: 60 passed, 1 opt-in test skipped. The skip is not YOLO mission evidence.
- Frontend: TypeScript and Vite production build passed, with the existing size warning.
- Asset tests include ambient/diffuse maps, missing dependencies, unsafe paths,
  bundle discovery, redacted provenance, failed downloads and interrupted publication.

Reproduce actual sponsor rendering and image-mode missions on isolated servers:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8001
cd frontend
RESCUETWIN_API_URL=http://127.0.0.1:8001 npm run dev -- --host 127.0.0.1 --port 5174
# in another terminal, repository root:
.venv/bin/python scripts/verify_textured_scenes.py
.venv/bin/python scripts/verify_integration_ui.py --mission-only --model 9A46aT7FCPg --output data/validation/textured-mission-default --timeout 180
.venv/bin/python scripts/verify_integration_ui.py --mission-only --model 5p6KKvHqh3W --output data/validation/textured-mission-second --timeout 180
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Browser and mission results are recorded below after execution. Rendered scan imagery,
synthetic people/fire, normalized exposure and analytic development comparisons must
not be described as real-world emergency safety validation.

### Actual source-render evidence

`verify_textured_scenes.py` passed default → second → default with no page errors or
failed assets, and backend acknowledgement of each RGB-D sequence after switching. Operator and ego frames were visually inspected: source floor finishes,
walls, furnishings and wood door surfaces are present and scene-specific. The first
run exercised cutaways at 1.0/1.6/2.2 m and off. Captures and the report are in ignored
`data/validation/textured-scenes/`. Each scene produced 196,608 finite positive depth
pixels (512×384). Three camera rays per scene were compared to the registered source
triangles; maximum error was below 0.0006 m. Returning to the default reproduced its
mesh filename and sampled depths. These numerical comparisons verify registration
and linear depth on sampled rays, not completeness of the scan.

Early browser attempts failed because the backend had started before vision was
installed, and one capture timed out under software rendering. The final successful
check used a restarted vision-enabled backend and explicit scene/control responses.

The supplemental `verify_material_render.py` synthetic regression also passed:
74.70% green pixels, 146,766 valid depth pixels and 2.75 m maximum depth; operator
visibility changes preserve depth, and missing materials disable start. Its mocks
were updated for the current building-use API. This is supplemental loader evidence,
not the sponsor-texture acceptance evidence above.

The camera panel now clears the previous scene's frame during loading and ignores
late responses from an earlier scene version.

### Representative mission

The default scene's clear-view `image-yolo` mission passed: one confirmed detection,
one survivor evacuated, 6.0 m travelled, two route updates, no degradation and no
page errors. The authoritative completion time was 12.0 simulated seconds. The
scenario is no-fire and uses a synthetic avatar; it establishes mission continuity
with furnished geometry, not fire robustness. Evidence:
`data/validation/textured-mission-default/browser-report.json`, scenario and captures.
CPU inference threads were bounded with the environment variables shown above.

The second scene's clear-view mission command exited nonzero before starting: no
valid free survivor placement exists 1.5–3.5 m ahead within ±25° of its existing
entry start. No start, exit or collision threshold was altered to force that test.
This is a limitation of that test scenario; a completed second-scene YOLO rescue
is not claimed. Its actual textured operator/ego frames, synchronized RGB-D and
source collision validation passed independently. Neither selected scene failed
source-geometry validation or remained untextured. No third model was needed: the
two selected exports already exercise distinct geometry, materials and texture counts.

### Upstream compatibility check

Merged `origin/main` at `8ef67d3` (search memory/group rescues), preserving camera
feed clearing and stale-response guards. `npm run build` passed; backend tests:
61 passed, 1 skipped. The actual textured-scene browser check passed default →
second → default against the merged backend (isolated ports 8003/5176), with all
RGB-D sequences acknowledged, matching mesh-ray depths and no page/asset errors.
