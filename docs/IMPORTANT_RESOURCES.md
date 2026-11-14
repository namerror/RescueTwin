# Important Resources
The CoStar real estate data is a raw data dump of real estate information that can be linked to the Matterport spaces and Vexcel regions
The Matterport API Keys can be used to harvest data about spaces from imagery to room sizes and classifiers
The Matterport SDK Keys can be used to enhance the 3D experience in the browser
The Vexcel API Keys can be used to access and visualize aerial capture data over large regions

## Matterport 
- https://beta-app.matterport.com/2026-princeton-hackathon/quickstart
- https://developers.matterport.com/api/
- https://developers.matterport.com/overview/
- check [downloaded examples](/matterport_examples/)
  
## Vexcel
- check [downloaded resources](/vexcel_resources/)

## Local resources (ignored by Git)

The downloaded CoStar kit is at `data/costar-hackathon-kit/`. Ordinary `rg --files`
will omit it; use `rg --files --hidden --no-ignore data/costar-hackathon-kit`.
Start with its `index.md`, then dataset model docs, schemas, and enum mappings.
See [AGENTS.md](../AGENTS.md) for joins and credential-name aliases. Never dump `.env`.

Offline inspection: `python3 scripts/inspect_sponsor.py MODEL_ID`.
Matterport metadata preparation: `python3 scripts/fetch_matterport.py MODEL_ID`.
