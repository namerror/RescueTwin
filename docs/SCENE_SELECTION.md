# Scene selection: shortlisted scans (inspected 2026-11-14)

Gate from [MVP](MVP.md): two defensible floor-level egress endpoints, connected walkable
topology. A balcony or window is not an exit. Reject rather than invent geometry.

Evidence sources (saved, git-ignored, under `data/prepared/<model>/source/` by
`scripts/prepare_scene.py fetch`): Matterport wall graph and rooms, photogrammetric and
schematic floor plans (image origin + 100 px/m in the model frame), and 2k skybox faces
of the panos nearest each candidate opening. The schematic DXF contains wall lines only,
with no door entities; the wall graph has only `wall` edges. Openings were therefore
identified from the plan images and confirmed visually in panos.

Asset correction (2026-11-14): `model.bundle(id: "mp:matterpak").assets.url`
provides complete textured packages for both selected scans. The earlier
`assets.resources(format: "matterpak")` query did not establish export availability.
Both packages now pass furnished-mesh import validation; see DATA_INTEGRATION.md.

## 5p6KKvHqh3W: 512 Veteran Ave (PropertyId 4025799, plan 2669568 "H", maxOccupants 5)

| Opening | Model-frame location (approx.) | Evidence | Verdict |
|---|---|---|---|
| Unit entry door (solid wood, deadbolt) | near pano 0, (0.0, 0.0) m, bottom-left notch of living room | pano 0 face 2 | Exit 1 |
| Balcony slider (room 7) | west side, pano 4 (−1.1, 2.5) m | pano 4 faces 1–2: upper-level balcony over an interior courtyard of a multi-storey building | Not an exit |
| Angled bottom-left wall | near pano 2 (2.6, −0.9) m | pano 2 face 3: corner fireplace | Not an opening |
| Dotted lines on south walls of rooms 3/4 | — | photogrammetric plan: windows | Not exits |

Result: **one** egress endpoint. Fails gate.

## 9A46aT7FCPg: 1276 W 38th St, unit 2 (PropertyId 22572250, plan 6106437, maxOccupants 8)

| Opening | Model-frame location (approx.) | Evidence | Verdict |
|---|---|---|---|
| Unit entry door (panel door, smart lock) | near pano 0, (0.0, −0.1) m, top-left of living room | pano 0 face 2 | Exit 1 |
| Room 9 "outdoor_other" | west end, pano 2 (−1.5, −1.8) m | pano 2 faces 3–5: elevated balcony with railing, street below | Not an exit |
| East end of hallway | pano 24 (14.7, 0.1) m | pano 24 faces 1–4, pano 25: blank wall between bedroom doors C and D | No opening |

Result: **one** egress endpoint. Fails gate.

## Decision (2026-11-14)

Neither shortlisted scan meets the two-exit requirement; both are upper-floor units.
The project owner chose to **relax the gate to one verified exit and use `9A46aT7FCPg`**
(the larger, more complex 4BR layout, matched maxOccupants 8).

Consequences, stated wherever results are shown:

- Evacuation ends at the single unit entry door (approx. x −1.0…0.1 m, y ≈ 0.7 m,
  top-left wall of the living room). Corridor, stairs, and street beyond it are assumed
  available and were not inspected.
- The balcony (room 9) is not an endpoint. A fire that blocks the entry door leaves
  survivors unresolved; there is no alternative route to report.
- This is a single-egress rehearsal and is not evidence of code-compliant egress.

Both scenes use their own furnished MatterPak OBJ, matching MTL and source JPEGs.
Collision starts from schematic DXF/PNG and is reconciled with scan obstacles;
room polygons and observation points retain the Matterport model frame. Thin scanned
door leaves in evidenced portals are treated as openable and recorded. The floor is
part of the textured scan, not a schematic image overlaid on source geometry.
See [data integration](DATA_INTEGRATION.md) for commands, measurements and limits.
