# Mesh and dataset handoff

## Human appearances

The app now supports six distinct source meshes: adult man/woman, boy/girl, and
older man/woman appearances. Select **Survivor appearance**, then **Apply**. Choose
**Mixed** and place six survivors to get one of each, repeated in placement order
for larger scenarios. The original adult-man asset remains the default.

Appearance is stored as `scenario.human_variant`, validated server-side, exported
with the scenario and frozen with paired-run inputs. It affects rendered people,
not search priors, mobility, medical state or agent tools. YOLO detects the person
class; it does not infer the authored age/gender appearance labels. Older roles are
selected from the source Adults category, without asserting exact ages.

`frontend/public/models/humans.json` pins six GLB hashes, source mesh names and
heights. Five variants were converted from original Microsoft Rocketbox FBX/texture
assets at revision `0943055db6ec570bcef9f2c8b41c9e5467c808f9`; the existing adult-man
conversion is retained. Attribution and the MIT notice are in
`frontend/public/models/ATTRIBUTION.md`. New meshes preserve skinning and the source
standing pose, use embedded 1024 px diffuse textures and hair opacity, and are
centered/grounded at their declared heights. No animation is played.

`./setup.sh` verifies all six cached files. Missing variants can be reproduced by
`scripts/prepare_humans.py`, using pinned original source hashes and Blender 5.2.2
(`BLENDER_BIN` overrides its path). Warm startup needs no Blender or network. A
second conversion of the male-child source reproduced the exact GLB checksum.
All mesh binaries, downloaded sources and captures remain ignored.

### Executed human checks

- Real `PersonDetector` / YOLO26n detected all six meshes from four viewing angles:
  **24/24**, confidence approximately **0.78–0.92**. The test imports the actual
  application loader, renders at 512×384 from 2.5 m with the camera at 1 m, and checks
  height and grounding. This is a plain synthetic component fixture, not sponsor
  imagery or evidence of smoke/occlusion robustness.
- **6/6 no-fire, one-survivor image-YOLO missions passed** in the default textured
  scan, with one evacuation per variant and no degraded mode. Evidence:
  `data/validation/humans/all-variants-missions.json` and `component-report.json`.
- Mixed mode rendered one of each variant in a single group fixture, and YOLO
  detected **all six**. Evidence: `mixed-six.png` and `mixed-report.json` in that
  folder. This is a synthetic group-render check.
- The actual UI selector, Apply, six-person JSON export and appearance lock during
  a run passed. The handoff app is idle on the complete condo with a six-person
  Mixed scenario. Evidence: `ui-report.json`, `mixed-six-scenario.json` and
  `mixed-ui.png`.
- Initial automation used direct API resets, leaving the browser camera's local
  sequence counter unchanged. The adult-man retry uses the normal UI Reset flow;
  it passed. Initial reports remain preserved. These runs do not establish
  cold-start latency or real-time inference performance.

## Recorded-fixture search and joins

Searches stream the ignored kit and retain exact ListingId/model joins. The new
`scripts/residential_context.py` checks `residential-listings.csv` ListingId,
`residential-listings.id`, Matterport `media[].videoKey` with reference type 36/234,
and `residential-listing-features.id`. Only MLS-source fixture indicators are mapped.

Feature IDs distinguish fire sprinklers (`2966`) from lawn irrigation (`3247`,
`3248`, excluded). Gas heating (`2893`, `2706`), gas water heating (`1620`) and gas
cooking (`1602`, `1604`) remain separate. A value-free feature is interpreted as
presence only as specified by the kit schema; explicit false remains false,
uninterpreted non-boolean values and conflicting indicators remain unknown.
Each field retains source ID, record type, feature ID/name, field path and
derivation. Contacts are excluded. Attributes apply to the home, not known fixture
positions within individual rooms, and do not establish fixture condition.

### Complete fixture-backed condo

`TSpwHx7D8yC`, **3505 Sage Road, Unit 2002**, ListingId **229913690**:

- The dataset teammate's prepared geometry passes 96.63% source floor coverage,
  99.52% wall agreement, all required connections, and zero skipped rooms.
- This handoff adds the exact residential property card and recorded MLS gas
  heating (`features[64]`, ID 2893), fire/smoke detector (`features[66]`, ID 2965)
  and fire-sprinkler (`features[67]`, ID 2966) fields. The existing room-risk module
  consumes these building defaults; fixture locations are not invented.
- The annotation remains a single verified unit-entry rehearsal. The corridor
  beyond that door is assumed available. Capacity stays unknown.
- The actual source-textured image-YOLO mission passed: one detection, one
  evacuation, 3 m travel, 6.0 simulated seconds and no degraded mode. The initial
  two-scene report is preserved at
  `data/validation/catalog-simulations/fixture-scenes-initial.json`.

### Newly prepared house scope

`AqPtqdwurj5`, **8142 Amor Rd**, ListingId **236478974**, is newly prepared from
its full textured MatterPak. It has a verified wooden double front door, inspected
in panoramas 1/3, and recorded MLS fire/smoke detector (`features[36]`, ID 2965) and
fire-sprinkler (`features[37]`, ID 2966) attributes.

Mesh reconciliation passes **93.21% floor coverage**, **99.40% wall agreement** and
all required start/entry connections. Navigation is explicitly scoped to seven
connected indoor rooms. One bedroom, one bathroom, one closet and one hallway are
unreachable on the source-derived base map and stay blocked; this is not a
complete-house rehearsal. `scope_note` records that limitation in the annotation
and runtime assumptions. Distance-only and risk-aware no-fire fixture-detector
missions both passed; see `new-scene-navigation.json` in the human-validation folder.
The source-textured image-YOLO rescue also passed: one survivor evacuated, 15.5 m
travel and 35.2 simulated seconds, without degraded mode. The first check's 180 s
wall-clock budget expired mid-route; the rerun allowed 420 s for software rendering
and completed. No real-time throughput claim follows from that allowance.

Combined source-scene evidence: `data/validation/humans/fixture-scenes-final.json`.
Backend regression suite: **107 passed, 1 optional test skipped**. Final focused
appearance, source-join, preparation and asset checks passed **20 tests**. The
frontend TypeScript/Vite production build passed with the existing size warning.

### Other inspected candidates

- `gDJYBZm1Hpy`: gas heating, detector and fire-sprinkler attributes; two scanned
  floors, so not promoted through the single-floor importer.
- `SpFLZ3933hU` and `2zG1AKPywRP`: gas heating/water heating, detector and fire
  sprinklers; three floors each, not promoted.
- `M4pMBmarVah`: recorded detector/fire-sprinkler attributes and a complete
  downloadable textured package. Entry panoramas were inspected, but the schematic
  opening did not reconcile with the entry position. No runnable bundle was
  published and no exit was invented.

Ignored search evidence: `data/prepared/fixture-search.json`,
`fixture-shortlist.json` and `fixture-candidates.json`.

## Reproduce

```sh
.venv/bin/python scripts/prepare_humans.py
.venv/bin/python scripts/prepare_catalog.py AqPtqdwurj5 --phase build
# Restart backend after publishing bundles or changing API scenario fields.
.venv/bin/python scripts/verify_humans.py --url http://127.0.0.1:5178
.venv/bin/python scripts/verify_catalog_simulations.py --url http://127.0.0.1:5178 --models TSpwHx7D8yC AqPtqdwurj5
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Browser checks require the `e2e` extra and Playwright Chromium. Use isolated
backend/frontend ports, one camera-driving browser, and bounded inference threads.
The synthetic component check, rendered-scan mission check and navigation fixture
check are separate evidence categories. No hazard-module or furniture-fuel
contract changes are made by this mesh/dataset work.
