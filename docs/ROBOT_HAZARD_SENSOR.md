# Robot hazard sensor

This document describes the sensor prototype in the local RescueTwin simulator.
The sensor follows the robot. It reports distance and heat at the nearest
eligible ignition point, plus smoke at the robot.

## Readings

| Field | Meaning |
| --- | --- |
| `position` | Robot grid cell `[column, row]`. |
| `time_s` | Mission clock when the sample was taken, in simulated seconds. |
| `radius_m` | Horizontal sensing radius, currently 1 metre. |
| `source_distance_m` | Straight-line distance between the robot and source cell centers, in metres. |
| `source_heat_intensity` | The selected source cell's current heat value on the simulator's 0–1 scale. |
| `smoke_intensity` | The robot cell's current smoke value on the simulator's 0–1 scale. |

## Source selection

For each configured ignition cell, the sensor computes the Euclidean distance in
grid cells and multiplies it by the scene resolution in metres per cell.
Ignition points qualify when they are at most 1 metre away and their current
heat is greater than zero. The closest point wins, with higher heat breaking
distance ties. If none qualifies, both source fields are `null`.

The source reading uses distance and the source cell's heat directly. Walls do
not affect this source selection. Separately, the existing planner receives
heat and smoke grid samples within 1 metre only where its line-of-sight check
passes. Heat in cells beyond the configured ignition points can enter those
local samples as fire spreads.

## Simulator interface

In the prototype, `GET /api/observations` includes a `hazard_sensor` object for
the bot. `GET /api/state` includes the same object for the viewer. The
prototype samples at initialization, during sensing, and after the robot
moves. The readings describe the simulator grid. The interface reports smoke
intensity directly and omits a visibility estimate.
