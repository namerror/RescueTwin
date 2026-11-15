# RescueTwin contributor guide

## Current scope

Read [architecture](docs/ARCHITECTURE.md), [user guide](docs/USER_GUIDE.md),
[data setup](docs/DATA.md), and [development reference](docs/DEVELOPMENT.md).
Inspect source and executable checks before reporting a capability as working.
Prioritize hazard awareness, potential-survivor identification, and adaptive evacuation:
one floor, one kinematic robot, fire/smoke, and simple survivor escort.
No carrying, manipulation, structural collapse, trained locomotion, or SLAM.

## Private inputs

Sponsor data cannot be published. `data/`, `scenes/`, `matterport_examples/`, and
`vexcel_resources/` are ignored and remain local. Do not delete them during cleanup.
Keep real model/property IDs, addresses, source downloads, weights, recordings,
original captures, and private scene manifests out of tracked files. Use synthetic
identifiers in tests and anonymized labels in publication media. Back up private
files before changing tracking or annotations.

For kit discovery, use `rg --files --hidden --no-ignore data/costar-hackathon-kit`.
Read its `index.md`, relevant `models/<dataset>.md`, `schemas/<dataset>.schema.json`,
`schemas/enums.json`, and `concepts/data-quality-and-confidence.md` first. Stream
JSONL records rather than dumping or expanding the full kit.

Commercial joins: CSV `PropertyId` → Commercial property `id`, plus exact attachment
`videoKey`. Residential joins: CSV `ListingId` → Residential listings `id`, plus
matching media `videoKey`; `ResidentialListingId` is a grouping key. Apartment
floor plans join `apartmentModels[].id` → `propertyApartment.models[].id`.
Never infer a match from address alone. Matterport reference enums 36 and 234
identify scan/streaming keys.

Preserve source ID, record type, field path, units, and derivation in local provenance.
Null and enum 0 follow the schema: unknown sprinklers are not false, structural
material is not interior fuel, and marketed capacity is not live occupancy.
Sponsor contact details must not be presented as real people.

## Credentials and knowledge boundaries

Use `.env.example` for names. Never print `.env`, `.aws`, credentials, or signed URLs.
`MATTERPORT_API_KEY` / `MATTERPORT_API_TOKEN` take precedence over `MP_API_KEY` /
`MP_API_TOKEN`; the key is the Basic Auth user and the token is its password.
Keep API tokens and `GEMINI_API_KEY` server-side. Browser SDK credentials are separate.

Only operator/evaluation views may read simulator placements and complete hazard
fields. Agent tools consume observations and belief. Static geometry and known
localization are explicit assumptions. Never expose survivor entity IDs/positions
as person detections. Browser RGB-D is rendered from scans, not live imagery;
synthetic and analytic fixtures must remain labeled and excluded from image claims.
The runtime loads `docs/ROBOT_CONSTITUTION.md`; retain that path and the controller's
mandatory human escort limits.

Freeze scenario inputs for paired runs. Compare policies with the same scenario,
sensors, hazard seed, and immediate safety rules. Do not attribute the whole routing
difference to an LLM or call simulated exposure real-world safety validation.
Do not initiate hosted experiments without explicit authorization and a new approved
local campaign; completed campaigns stay closed. Replay requires no model calls.

## Verification

Use `./setup.sh` with authorized local inputs. For manual development, install
`pip install -e '.[test,scene,vision]'`, run `uvicorn rescuetwin.api:app --host
127.0.0.1 --port 8000`, and start `npm run dev` from `frontend/`.
Check with `python3 -m pytest -q` and `cd frontend && npm run build`.
Offline tests must work without private annotations or sponsor downloads. Optional
sponsor checks use `RESCUETWIN_TEST_BUNDLE` and `RESCUETWIN_TEST_ANNOTATION`.
Keep generated artifacts ignored. Prioritize a complete mission over deferred layers
or visual polish; retain A* as the navigation fallback.
