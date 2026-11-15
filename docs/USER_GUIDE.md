# Using RescueTwin

RescueTwin is a local workbench for rehearsing a simulated search and evacuation inside a building scan. Start with the [README quickstart](../README.md#quickstart) and [private-data setup](DATA.md).

## Run a rehearsal

1. Select a locally prepared **Scene**. Wait for the textured geometry and camera to load.
2. Open **Mission settings**. Choose a preset or place survivors and ignition points on the floor. Apply changes before starting. Survivor appearances and building use are scenario inputs.
3. Select **Baseline**, **Mission A***, or **Gemini**, and choose distance-only or risk-aware routing separately. Use **YOLO · RGB-D** for image-based person detection.
4. Start the run. Inspect room coverage, detected people, route changes, and mission events. Pause to examine the scene; reset before editing it.
5. Review evacuation accounting and unresolved people. A reported detection or assistance request is not an evacuation.

A scene’s capacity presets use a fraction of its **marketed maximum occupants**, when available and consistent. They create hypothetical workloads, not estimated live occupancy. A preset may place fewer people if valid placements are limited.

## Understand the views

- **Overview / Plan:** orbit or inspect the building from above. Operator cutaways affect the overview; the robot camera retains full scanned geometry.
- **Follow robot / POV:** inspect the robot’s route and rendered perspective. Keep the browser tab open: it supplies RGB-D frames to the backend.
- **Operator truth:** displays simulated people and hazards, including visibility aids through walls. These overlays are excluded from robot-camera perception.
- **Robot belief:** shows discovered information. Unseen people and unmeasured hazards remain unknown.
- **Property context:** displays source building attributes. Unknown sprinkler information is not evidence of absent sprinklers. Construction material is not interior fuel load.

Simulation-speed controls change the target wall-clock pace while retaining the simulation timestep. Rendering and inference can limit achieved speed.

## Choose a method

| Method | Mission decisions |
| --- | --- |
| Baseline | Nearest unsearched room first; escort confirmed people to an exit. |
| Mission A* | Plans remaining mission goals using travel cost, survivor likelihood, and observed hazard urgency. |
| Gemini | Proposes room, confirmed-survivor, or exit goals from robot observations. The controller validates and executes them, with deterministic fallback. |

All use the existing navigation controller. Risk-aware routing adds observed heat/smoke costs; distance-only routing emphasizes travel distance. Human escort limits remain mandatory in both modes. The robot may traverse conditions it cannot escort a person through. A blocked escort holds and requests simulated assistance rather than relaxing human limits.

Gemini requires the server-side configuration described in [Data and setup](DATA.md#optional-gemini). The default decision-quality mode pauses the mission clock during inference; do not interpret its mission time as real-time response. Separate tool-backed Gemini experiments are available to developers and retain their own approval gates.

**Analytic fixture** perception is a development mode that uses simulator information. It is not evidence of image detection. YOLO detects synthetic avatars in browser-rendered scan imagery, not live people or live camera footage.

## Compare and replay

**Compare strategies** runs distance-only and risk-aware routing on the same frozen scenario, sensors, hazard settings, and immediate safety rules. It does not compare the three mission algorithms or include hosted Gemini reasoning. Keep the rendering tab open during image-mode comparisons.

Runs record locally by default. Disable **Record next run** to opt out. Under **Recorded runs**, refresh the list, select a run, and choose **Replay**. Play, pause, seek, step, or adjust replay speed. Historical views are read-only; choose **Live** to return to the current mission. Exported recordings contain private scene information and should stay local.

## Limits

The prototype assumes static scanned geometry, known localization, manually reviewed exits, and survivors who can follow. It does not perform SLAM, carrying, manipulation, structural-collapse simulation, or real responder communications. Hazard/exposure values are illustrative simulation quantities, not medical or building-safety measurements. A verified unit exit does not establish safe egress beyond the scanned scope.
