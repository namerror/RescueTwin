# Run RescueTwin with Gazebo

## Pick a floor and launch

From the repository root, run:

```bash
./setup.sh --gazebo
```

1. Choose the **Public two-room floor** or one of your prepared private scans.
2. Click the floor map to add or remove ignition points. **Fire demo** places the public preset. **Clear air** removes all ignitions.
3. Select **Launch Gazebo**. The setup screen prepares the run and opens Gazebo and RViz. RViz shows the robot’s RGB and depth views.

Use **Stop** in the setup screen to return to the map and move an ignition. RescueTwin remembers the last launched setup for each floor. The screen shows the policy client file when the camera is ready. Closing the browser leaves the simulation running. Ctrl+C in the launcher terminal stops both. If a browser window does not open, use the local URL printed in the terminal.

## One-time system setup

Use Ubuntu 24.04 with [ROS 2 Jazzy](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html) and [uv](https://docs.astral.sh/uv/getting-started/installation/). After adding the ROS apt source, install:

```bash
sudo apt install ros-jazzy-ros-gz ros-jazzy-rviz2 python3-colcon-common-extensions
```

The first launch builds the ROS package, sets up its Python environment, and downloads TurtleBot3 assets if needed. The public floor is included. For a private scan, follow the [private data setup](../docs/DATA.md) once. The picker lists compatible, validated local OBJ bundles and previously prepared Gazebo floors. Materials and scan registration follow the [Gazebo bundle requirements](ADVANCED.md#use-a-private-floor).

See the [Gazebo command-line reference](ADVANCED.md) for headless setup, custom paths, and scripted runs.

## Use your own policy

Start with the [Python example](examples/policy_client.py). Read the run URL and
bearer token from `policy-client.json`, then send the token in the
`Authorization: Bearer …` header. Keep this file local. The API is intended for a
trusted developer on the same machine.

| Route relative to the run URL | Method | What to send or read |
| --- | --- | --- |
| `/map` | GET | Static map origin, size, resolution, and blocked `[column, row]` cells. |
| `/observations` | GET | Observation sequence, simulation timestamp in nanoseconds, pose, twist, local heat and smoke, and camera status. |
| `/frames/latest` | GET | A synchronized RGB-D frame when `ready`. A `missing` or `stale` response carries no frame. |
| `/lease` | POST | Send `{}` to acquire the one-second motion lease or `{"lease_id":"…"}` to renew it. |
| `/commands` | POST | Send a unique `command_id`, `lease_id`, recent `observation_seq`, and a command type from the list below. |
| `/commands/{command_id}` | GET | Command status, reason, and requested and applied velocity as `[m/s, rad/s]`. |

- `navigate_to_pose` takes `x`, `y`, and `yaw`. Renew the lease while waiting for completion.
- `velocity` takes `linear_mps` (up to ±0.15 m/s) and `angular_radps` (up to ±1 rad/s).
  Send a fresh command at about 10 Hz. Each command expires after 250 ms.
- `stop` takes no motion fields. A new command supersedes the current one. Switching
  control modes inserts one zero-motion tick. HTTP 409 indicates a conflict such as
  an expired lease or stale observation sequence.

Frames include the capture timestamp, camera intrinsics, and fixed mount. RGB is
base64-encoded packed RGB8. Depth is base64-encoded little-endian float32 in metres,
with zero for invalid pixels. Both arrays are row-major. The camera renders flame
and smoke particles in ignition runs and reports `gazebo_particle_rgbd` as frame
provenance. Particles can scatter some depth returns. The local probes report
normalized floor heat and lower-layer smoke. Gazebo advances the hazard field
from its clock in 0.2-second steps. Existing clear-air runs and newly generated
clear-air or hazard runs use the renderer selected in their prepared world.
The API exposes observations and static map data. Evaluator state stays on the
operator side.
