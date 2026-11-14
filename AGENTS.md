# RescueTwin contributor guide

## Status and scope

The accepted hackathon design is documented in [architecture](docs/ARCHITECTURE.md),
[MVP](docs/MVP.md), and [implementation guide](docs/IMPLEMENTATION.md). These are
implementation targets, not evidence that the application is complete. Inspect the
current source and executable checks before reporting any capability as working.
The runnable app defaults to the prepared sponsor scene `9A46aT7FCPg` (single verified exit; see docs/SCENE_SELECTION.md) with YOLO on browser-rendered RGB-D. Both selected scenes have validated textured MatterPak bundles (see docs/DATA_INTEGRATION.md). Browser RGB-D is rendered from scans, not live imagery; the synthetic fixture/analytic detector are labelled development modes.

Priorities: risk detection, potential-survivor identification, and adaptive evacuation
routing. One floor, one kinematic robot, fire/smoke, and simple survivor escort.
No structural destruction, carrying, manipulation, trained locomotion, or SLAM in MVP.
The physics teammate owns the hazard module; agree its contract before integration.

## Ignored sponsor data discovery

`data/` is ignored by Git. Ordinary file discovery can miss the kit. From repo root:

```bash
rg --files --hidden --no-ignore data/costar-hackathon-kit
cat data/costar-hackathon-kit/index.md
rg --files --hidden --no-ignore matterport_examples vexcel_resources
```

Read the kit's `index.md` first, then `models/<dataset>.md`,
`schemas/<dataset>.schema.json`, `schemas/enums.json`, and
`concepts/data-quality-and-confidence.md`. Stream JSONL records rather than dumping
large files. Avoid expanding all datasets into thousands of files unnecessarily.

Commercial join: CSV `PropertyId` → commercial-property `id`, then verify the same
`MatterportModelId` against attachment `videoKey`, including nested apartment
models/units or commercial listings linked by `propertyId`. Residential join:
`ListingId` → residential-listings `id`, then matching media `videoKey`.
`ResidentialListingId` groups listings; it is not the MLS record key. Matterport
reference enums 36 and 234 identify scans/streaming keys. Apartment listings join
via `propertyId`; floor plans join via `apartmentModels[].id` to
`propertyApartment.models[].id`. Never infer a floor-plan match from address alone.

Preserve source ID, record type, field path, units, and derivation in scene provenance.
Missing/null values and enum 0 must be interpreted using the schema, never silently
converted to false or known values. `hasSprinklers: null` is unknown; structural
material is not interior fuel. `maxOccupants` is a marketed capacity, not live occupancy.
The kit describes real property data but substitutes fake contact emails/phone numbers.
Do not present those contacts as real people.

## Credentials and assets

Read `.env.example` for variable names; never print `.env`, `.aws`, tokens, or signed
asset URLs. Repository names `MP_API_KEY` / `MP_API_TOKEN` correspond to the notebook's
`MATTERPORT_API_KEY` / `MATTERPORT_API_TOKEN`. The metadata loader accepts either
pair and prefers the canonical `MATTERPORT_*` pair. Never log values. API key is the Basic Auth user; token is the password. Browser SDK
credentials (`SDK_KEY`, `SDK_DOMAINS`) are separate. Keep API tokens server-side.
`VEXCEL_API_KEY` is optional for the deferred exterior layer. Add a server-side
`GEMINI_API_KEY` and configurable `GEMINI_MODEL` when implementing reasoning.
Do not commit sponsor downloads, model weights, credentials, or generated captures.

## Knowledge and implementation boundaries

Simulator truth includes survivor/ignition placements and complete hazard fields.
Only operator views and evaluation may read it. Agent tools consume versioned local
observations and belief state; static scanned geometry and perfect localization are
explicit assumptions. Never implement person detection by exposing simulator entity
IDs/positions. Synthetic fixtures and oracle detectors must be visibly labeled and
excluded from claims of image-based perception or sponsor integration.

Editable scenarios must validate placements and freeze inputs during paired runs.
Compare distance-only and risk-aware routing with the same scenario, sensors,
hazard seed, and immediate safety rules. Do not attribute the whole difference to
the LLM or describe normalized simulated exposure as real-world safety validation.

## Verification and handoff

Install with `pip install -e '.[test,scene,vision]'`; run `uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8000`.
Run the frontend with `cd frontend && npm install && npm run dev`.
Check with `python3 -m pytest -q` and `cd frontend && npm run build`.
Optional extras `agent` and `vision` require external credentials/weights and separate validation.
Use its acceptance checklist for integration; keep generated artifacts out of source.
Prioritize a complete mission before FAR, Vexcel, or visual polish. FAR has a two-hour
post-baseline experiment budget and must retain A* as a fallback.
