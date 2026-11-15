# Publication media

These six assets are screenshots and recorded replay excerpts from RescueTwin. Scanned geometry and CoStar context come from the authorized local sponsor data; people, robot, fire, smoke, and sensor imagery are simulated.

| Asset | Source and treatment |
| --- | --- |
| `workbench.png` | Existing workbench screenshot, cropped to the stage; identifying scene title/selector replaced with a demo label. |
| `property-context.png` | Existing CoStar context screenshot; cropped to property attributes and address replaced with an anonymous label. Unknown source values remain visible. |
| `fire-and-smoke.gif` | Existing cropped hazard demonstration, preserved as an illustrative clip. |
| `baseline.gif` | Baseline recording, with risk-aware routing. |
| `mission-astar.gif` | Mission A* recording, with risk-aware routing. |
| `gemini.gif` | Planner-backed Gemini experiment recording, with risk-aware routing; distinct from the interactive supervisor. |

The three method clips use the same frozen scenario and interval, with 3× playback at 6 fps. They are excerpts, not complete missions or performance comparisons. Captions were added and identifying UI chrome/metric cards excluded; recorded scene states were not altered. Looping resets are expected. No new hosted inference was used to produce these clips.

Original captures, run identifiers, and the full provenance/check records remain in ignored local storage. Public media contains no credentials or source download links. Human asset attribution is maintained in [the model attribution file](../../frontend/public/models/ATTRIBUTION.md).
