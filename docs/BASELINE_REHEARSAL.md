# Baseline rehearsal — 2026-11-14

## Scope and reproduction

This checkpoint groups source-package validation and rendering, exact sponsor joins,
capacity presets and report provenance, the second scene configuration, and reproducible
browser checks. Sponsor downloads, weights, screenshots and raw reports remain ignored.
The machine-specific `.claude/launch.json` is outside this baseline commit.

Use Python 3.12 and Node 24.22.3. On this machine Node is installed under
`~/.local/opt/node-v24.22.3-darwin-arm64/bin`; add that directory to PATH if npm is absent.
Dependencies were already installed. Browser checks additionally need the `e2e` extra
and Playwright Chromium. Prepare the two sponsor scenes as described in DATA_INTEGRATION.md.

```sh
.venv/bin/python -m pytest -q
(cd frontend && npm run build)
# Separate terminals, isolated from the existing app:
.venv/bin/python -m uvicorn rescuetwin.api:app --host 127.0.0.1 --port 8001
(cd frontend && RESCUETWIN_API_URL=http://127.0.0.1:8001 npm run dev -- --port 5174 --strictPort)
.venv/bin/python scripts/verify_integration_ui.py --url http://127.0.0.1:5174 --timeout 180 --output data/validation/baseline-rehearsal-final
.venv/bin/python scripts/verify_material_render.py
```

## Checks on the reviewed tree

- Backend: 46 passed, 1 opt-in YOLO fixture skipped. Starlette/httpx deprecation warning.
- Frontend: TypeScript and Vite production build passed. JavaScript bundle 676.45 kB;
  the existing bundle-size warning remains.
- Initial browser pass: both sponsor scenes loaded with four presets each; clear-view
  YOLO mission evacuated 1/1 with one confirmed detection, two route changes, 6 m travel,
  zero exposure and no degraded flag. No JavaScript page errors. Screenshot inspected.
- Material fixture: 74.7% green RGB pixels, 146,766 valid depth pixels, maximum depth
  2.75 m. Operator walls toggle preserved depth; missing MTL disabled mission start.
  This is a synthetic material test, not sponsor imagery.

The expanded browser rehearsal also passed: scenario JSON was loaded and applied through
the UI, and the backend scenario matched it exactly. YOLO again confirmed and evacuated
1/1 with two route updates and no degradation. The analytic Standard run stopped at a
hazard at 49 s, held until conditions changed, resumed, and displayed route updates at
107.6 and 107.8 s. It was deliberately paused there; this segment is not a completed rescue.
Both scene loading and all rehearsal segments produced no JavaScript page errors.
The mission and route screenshots were visually inspected.

Capacity 25% paired results (seed 7, analytic development perception):

| Policy | Evacuated | Simulated time | Normalized exposure |
|---|---|---|---|
| Distance-only | 2/2 | 195.2 s | 163.279 |
| Risk-aware | 2/2 | 203.2 s | 164.975 |

Risk-aware routing was slower and had slightly higher exposure in this scenario.
The UI displayed the scenario hash and capacity provenance:
`commercial-property` 22572250, `propertyApartment.models[1].maxOccupants` = 8 people.
Frozen scenario SHA256: `8ef806fd6e06dbb9debfd69164e81970cc0a6714fee1476323729b232e2e1ad6`.
Raw JSON reports, scenario and screenshots are in ignored
`data/validation/baseline-rehearsal-final/`; material evidence is in
`data/validation/material-fixture/`. No deployment or push was performed.

## Rehearsal flow and limits

1. Select the default 9A46aT7FCPg scene. Load the generated clear-view-scenario.json,
   review it and Apply. It places one synthetic survivor at [15, 16], with no ignition.
2. Choose YOLO RGB-D and start. Show confirmation, escort, evacuation and the route
   updates in the timeline. This is a deliberately clear camera view, not proof that
   every avatar placement, occlusion or fire scenario completes with YOLO.
3. Reset, apply Standard and choose the explicitly labeled analytic development mode.
   Show the route-update event during the evolving-hazard run. A route-change count
   alone does not isolate hazard causation or establish an adaptive advantage.
4. Reset, apply Capacity 25% and Compare strategies in analytic development mode.
   Show both outcomes, the frozen scenario hash and matched maxOccupants provenance.
   Capacity is hypothetical workload, not live occupancy; this comparison excludes YOLO
   and hosted Gemini. Report the actual metrics, including slower or higher-exposure runs.

The supported pitch remains a scan-derived single-floor rehearsal with one verified exit,
synthetic hazards and people, and normalized simulated exposure. Complete textured
Matterport geometry, obstacle/door/exit reconciliation, hosted Gemini and an end-to-end
hazard-driven YOLO advantage remain unverified. The broader MVP acceptance checklist is
not declared complete by this checkpoint.

## Next sponsor-data follow-up

Request a complete OBJ + matching MTL + all referenced textures for one chosen scan,
or a documented complete chunk manifest with material dependencies and coordinate frame.
Resolve the referenced MTL's HTTP 403 and MatterPak availability with the sponsor.
Then import using the existing validation workflow and reconcile closed doors/obstacles
with the start, observation points and verified exit. Preserve the existing bundle on
failure. No sponsor message or purchase was made. Vexcel and visual polish remain deferred.
