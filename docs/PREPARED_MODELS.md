# Textured simulation catalog

All 14 models from the original prepared-model library are now runnable, single-floor simulation scenes. Select any of them directly from **Scene**, or choose **Use in simulation** in **Browse prepared models**. Each uses its own furnished MatterPak OBJ, matching MTL, per-face UVs and source texture images. Downloads, source plans and captures remain ignored under `data/`.

## Validated scene bundles

| Model | Source name | Floor coverage | Wall agreement | Rooms | Mesh-skipped rooms |
|---|---|---:|---:|---:|---:|
| `5p6KKvHqh3W` | Plan H 2BR, 2BA - 932SF | 96.14% | 99.34% | 6 | 0 |
| `89mvy1QCyWW` | 1BR, 1BA - 700SF | 97.21% | 100.00% | 5 | 0 |
| `9A46aT7FCPg` | 4BR, 3BA - 1,613SF | 95.25% | 99.78% | 7 | 0 |
| `AgNa5Ub1oSg` | Plan A 2BR, 2BA - 1,000SF | 86.58% | 98.59% | 7 | 0 |
| `HjEyXryhRLk` | Suite A - 2,000SF | 100.00% | 100.00% | 2 | 1 |
| `JYdxYmrvGcF` | Plan C 2BR, 2BA - 952SF | 97.41% | 100.00% | 8 | 0 |
| `RpyW8iX8pSi` | 1BR, 1BA - 600SF | 98.69% | 100.00% | 3 | 0 |
| `SCkUhAc2SSU` | Plan D 2BR, 2BA - 896SF | 97.02% | 99.58% | 6 | 0 |
| `VMBauFXLwEx` | 2BD, 2BA - 830SF | 97.22% | 98.29% | 7 | 0 |
| `exa6GnTAtgT` | 4BR, 3BA - 1,613SF | 93.60% | 99.56% | 8 | 0 |
| `uUWZHYgM2yL` | 3BR, 1BA - 1,130SF | 99.26% | 100.00% | 7 | 0 |
| `uxxr5qNETDf` | Plan G 2BR, 2BA - 863SF | 95.80% | 99.65% | 6 | 0 |
| `wgRoz2VRhAq` | Plan D 2BR, 2BA - 896SF | 95.83% | 98.89% | 6 | 0 |
| `ypeAH5ecxpX` | 2BR, 2BA - 1,100SF | 98.42% | 100.00% | 6 | 0 |

Every bundle passes the existing 85% floor-coverage and 85% wall-agreement gates, furniture-aware obstacle projection, source-material checks and connected start/entry checks. Unscanned and disconnected cells stay blocked. The office suite `HjEyXryhRLk` has one additional room excluded after mesh projection because it has no reachable cell; this is recorded rather than silently made traversable. These checks are not a certified traversability model.

## Entry and floor scope

The 12 additional `scenes/MODEL.json` annotations record measured entry segments, source panorama faces, manual inspection and the beyond-door assumption. A balcony, window or other unreviewed opening is not counted as a second exit. Mission evacuation ends at the verified unit/suite entry approach; the corridor/street beyond is assumed available and was not inspected.

- `SCkUhAc2SSU`: the automatic bedroom polygon enclosed 6.165 m² beyond the kitchen/entry wall. Source photo floor plan and panoramas 0–3 were inspected. A recorded room clip removes that unscanned extension at the observed wall; every source room capture position is retained. Clips intersect existing polygons and cannot add floor.
- `uUWZHYgM2yL`: the nearest schematic entry cell intersected the scanned closed door. The endpoint moves 0.28 m to its free approach cell on the indoor side. The measured opening and door evidence are unchanged. The builder bounds an approach offset to two grid cells; source collision validation still applies.
- Scanned closed door leaves remain visible in robot RGB-D even where navigation assumes an evidenced door is openable. This pre-existing scan-versus-simulation limitation is not resolved by texture import.

## CoStar provenance

Each model is joined by its exact CSV PropertyId and Matterport attachment videoKey (reference type 36/234). Exact apartment-plan joins are used when present. `89mvy1QCyWW`, `HjEyXryhRLk`, `VMBauFXLwEx` and `uxxr5qNETDf` have property-level attachment joins without a matched apartment plan: their property cards retain field provenance, but floor-plan capacity, bedrooms/baths and plan area are not inferred. Null marketed capacity stays unknown. Source contacts are never included.

## Preparation and checks

The committed annotations and the existing sponsor credentials/kit can reproduce the bundles:

```sh
.venv/bin/python scripts/prepare_catalog.py
.venv/bin/python scripts/prepare_catalog.py --phase build
# Restart the backend after publishing new bundles.
.venv/bin/python scripts/verify_catalog_simulations.py --url http://127.0.0.1:5176
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

The downloader uses isolated subprocesses, bounded concurrency and cached source plans/panorama 0 plus full MatterPak packages. Logs and reports stay under ignored `data/prepared`. Additional panorama faces reviewed for the SCk correction can be retrieved with `prepare_scene.py fetch SCkUhAc2SSU --panos 0 1 2 3 11`.

The browser mission check uses the real app, the source textured geometry, rendered RGB-D, actual YOLO inference and a synthetic avatar in a no-fire scenario. It verifies one-person rescue continuity and depth/frame processing, not fire robustness, live imagery or emergency safety. Source-mesh validation reports remain in each model directory. Browser evidence and scenarios are in `data/validation/catalog-simulations/`.

The model library still supports clearly labelled preview-only entries if future unprepared downloads are added. Those entries cannot be selected as simulations until their own preparation passes. The current 14 catalog entries are all simulation-ready.

## Executed acceptance results (2026-11-14)

- **14/14 source-textured YOLO missions passed**: one synthetic survivor detected
  and evacuated per scene, no ignition, finite positive rendered depth, no degraded
  mission, no page errors or failed source assets. Combined report:
  `data/validation/catalog-simulations/all-scenes-report.json`. The operator-view
  contact sheet was visually inspected across all 14 models. Simulated completion
  times ranged from 3.0 to 28.2 seconds; these are not wall-clock throughput claims.
- **28/28 analytic-development navigation missions passed**: distance-only and
  risk-aware policies on the same frozen no-fire scenario in each scene. Evidence:
  `analytic-missions.json` in the same folder. These are fixture-detector checks.
- Backend suite: **95 passed, 1 optional test skipped**. Frontend TypeScript/Vite
  production build passed with the existing bundle-size warning.

The original second-scene clear-forward placement failure is superseded by an
actual successful mission at a valid line-of-sight placement. The controller is
allowed to turn and search before detection; no initial camera direction or
detector evidence is fabricated.

## Floor plan controls

**Plan** changes the active scene camera to an overhead view. **Import custom floor plan JSON…** imports the annotated metric format accepted by `rescuetwin.environment.parse_environment`; it creates a custom analytic-development map. It does not import an ordinary floor-plan image or PDF.
