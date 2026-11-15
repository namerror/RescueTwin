# Data and private setup

The demo uses data supplied by Matterport/CoStar for the hackathon. **That data cannot be published with this repository.** Sponsor datasets, reviewed property annotations, downloads, weights, recordings, and original captures remain local and Git-ignored. Public screenshots and replay excerpts use anonymized labels.

## Integrated sponsor layers

| Layer | Official dataset / interface | Role |
| --- | --- | --- |
| CoStar building/property data | **Commercial property** (`commercial-property`) | Property type, building attributes, apartment floor-plan characteristics, marketed capacity, and source context. |
| CoStar building/property data | **Apartment listing** (`apartment-listing`) | Corroborates the matched apartment floor plan and capacity; conflicts disable capacity-based presets. |
| CoStar building/property data | **Residential listings** (`residential-listings`) | MLS home characteristics, matched Matterport media, and residential context. |
| CoStar building/property data | **Residential listing features** (`residential-listing-features`) | Reported fixtures such as gas appliances and fire-sprinkler indicators, where explicitly present. |
| Matterport 3D digital twins | **Model API** (GraphQL), floor/room spatial information, schematic floor plans, **MatterPak bundles** | Metric scan geometry, room boundaries, textured OBJ/MTL assets, navigation preparation, and browser-rendered RGB-D. |

CoStar context supplies building-use search priors and hypothetical scenario inputs; Matterport supplies spatial environments. Fixture indicators are building/home-level reports, not verified fixture locations or operational conditions. Commercial listings, tenant/company records, and sale comps are not active mission inputs. Vexcel is deferred. The workbench renders imported assets using Three.js; it does not embed the Matterport browser SDK.

## Supply authorized local inputs

Obtain a private reviewed scene manifest from the project team, or prepare your own reviewed annotations. Keep actual model IDs and property identifiers in ignored files only.

```text
data/costar-hackathon-kit/   # authorized CoStar kit, including its index and schemas
scenes/                    # private reviewed annotations, if preparing scenes locally
matterport_examples/       # optional local sponsor examples
vexcel_resources/          # optional local reference material
```

Read the kit’s `index.md`, relevant model pages and JSON Schemas, `schemas/enums.json`, and `concepts/data-quality-and-confidence.md` before processing records. Stream JSONL records; do not expand the full kit unnecessarily.

Create `.env` from `.env.example` without replacing an existing configuration. Configure `MATTERPORT_API_KEY` and `MATTERPORT_API_TOKEN`: the API key is the Basic Auth username and the token is its password. Legacy `MP_API_KEY` / `MP_API_TOKEN` aliases are accepted; the canonical pair takes precedence. Keep API credentials on the server. Browser SDK credentials are separate and are not needed by this renderer.

## Import and launch

```bash
./setup.sh --import-scenes /absolute/path/to/private-scenes.json
```

The manifest contains model IDs, reviewed annotations, and a default model; it contains no downloaded assets or credentials. Import validates the full manifest, prepares missing scenes, and starts only when every scene succeeds. Missing assets require your own Matterport access and the local CoStar kit. Failed preparation preserves previously published bundles; retry after correcting the failure.

Set `RESCUETWIN_MODEL_ID` in ignored `.env` to the desired imported model for later launches:

```bash
./setup.sh
./setup.sh --install  # resynchronize dependencies after dependency changes
```

A complete validated cached bundle needs neither sponsor credentials nor the kit to launch. Cached bundles live under `data/prepared/`; imported annotations are embedded in `bundle/scene.json` and take precedence over `scenes/` annotations. To revise an imported recipe, edit the private manifest and import again.

Backend and frontend default to localhost ports 8000 and 5173. Configure `RESCUETWIN_BACKEND_PORT` and `RESCUETWIN_FRONTEND_PORT` in `.env` if needed. Exported environment values take precedence. `.env` accepts literal assignments, not shell commands or interpolation. The local server has no authentication; bind it to localhost.

## Prepare or share a scene

New scans require manually reviewed exits and valid annotations; downloading a scan does not certify an exit. With the optional scene dependencies installed:

```bash
.venv/bin/python scripts/prepare_scene.py fetch "$RESCUETWIN_MODEL_ID"
.venv/bin/python scripts/prepare_scene.py build "$RESCUETWIN_MODEL_ID"
.venv/bin/python scripts/scene_assets.py matterpak "$RESCUETWIN_MODEL_ID"
# Import the local OBJ path printed by the MatterPak command:
.venv/bin/python scripts/scene_assets.py import "$RESCUETWIN_MODEL_ID" /absolute/path/to/mesh.obj
```

These direct Python commands use exported environment variables; the launcher reads root `.env` automatically. Keep preparation outputs in ignored `data/`. Source mesh validation must pass before using a scene. Restart the backend after publishing new bundles.

To share reviewed recipes privately:

```bash
./setup.sh --export-scenes /absolute/path/to/private-scenes.json
```

Export checks local assets without downloading them. Recipients supply their own authorized data and credentials. Do not commit or publicly distribute this manifest.

## Source joins and uncertainty

Commercial joins use CSV `PropertyId` → Commercial property `id`, verified against the exact Matterport attachment `videoKey`. Apartment floor plans join `apartmentModels[].id` → `propertyApartment.models[].id`. Residential joins use CSV `ListingId` → Residential listings `id` and matching media `videoKey`; Residential listing features joins on that same listing ID. `ResidentialListingId` groups listings and is not the MLS record key. Never match a floor plan by address alone.

Local provenance preserves source ID, record type, field path, units, and derivation. Missing/null values and enum 0 follow the schema. Marketed capacity is not live occupancy, structural material is not interior fuel, and unknown sprinkler status is not false. Sponsor contact details are excluded from mission context.

## Optional Gemini

Set server-side `GEMINI_API_KEY` and optionally `GEMINI_MODEL` in `.env`, restart, select **Gemini**, and start a mission. Setup installs the agent extra; manual installs need `pip install -e '.[agent]'`. The interactive supervisor uses observations and controller-validated goals, with deterministic fallback. Never include the API key in browser code.

Tool-backed campaign experiments are separate from interactive missions. They require a new explicitly approved local campaign configuration, account quota information, and a budget. Completed campaigns cannot be reopened by merely replaying recordings. Documentation builds and replay capture require no model calls.
