# Run RescueTwin with Gazebo and RViz

Start with the public two-room floor and a TurtleBot3 Burger. The example can send
a destination to RescueTwin’s built-in A* follower or drive the robot directly
with linear and angular velocity commands. Both pass through the collision gate.

The public floor runs from this checkout with the TurtleBot3 model package. It needs
no sponsor data or credentials.

## 1. Set up the workspace

Use Ubuntu 24.04 with ROS 2 Jazzy, Gazebo Harmonic, RViz, `ros_gz`, `colcon`, and
`uv`. The desktop quickstart opens both viewers. The optional headless launch uses
`xvfb-run` to keep RGB-D rendering available. [Install ROS 2 Jazzy](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html) ·
[Install uv](https://docs.astral.sh/uv/getting-started/installation/)

After adding the ROS apt source, install any missing simulation and build tools:

```bash
sudo apt install ros-jazzy-ros-gz ros-jazzy-rviz2 python3-colcon-common-extensions xvfb xauth
```

In **terminal 1**, open Bash at the repository root. Build and activate the workspace:

```bash
source /opt/ros/jazzy/setup.bash
uv sync --locked --inexact --python /usr/bin/python3 --extra ros-policy
(cd ros2_sim && /usr/bin/colcon build --packages-select rescuetwin_sim --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3)
source ros2_sim/install/local_setup.bash
```

Set the model path and extract the TurtleBot3 assets if they are missing. This
uses the ROS apt source and keeps the files in the project’s ignored data directory.

```bash
robot="$PWD/data/ros2_sim/robot"
models="$robot/opt/ros/jazzy/share/turtlebot3_gazebo/models"
if [ ! -f "$models/turtlebot3_burger/model.sdf" ]; then
  mkdir -p "$robot"
  (
    cd "$robot" &&
      apt-get download ros-jazzy-turtlebot3-gazebo &&
      dpkg-deb -x ./ros-jazzy-turtlebot3-gazebo_*.deb .
  )
fi
```

## 2. Launch the simulator and viewers

In **terminal 1**, run:

```bash
ros2 launch rescuetwin_sim bringup.launch.py
```

Bring-up prepares the public two-room floor with the TurtleBot3 Burger on first
launch and reuses it afterward. Gazebo shows the simulation world. RViz shows
the robot’s first-person RGB and grayscale depth on the left, with the robot’s
axes and a reference grid in the center.

Leave terminal 1 running. When Uvicorn reports that it is listening on
`127.0.0.1:8091`, the policy example can connect. The launcher prints the path
to `policy-client.json`, which the server writes when it starts. Closing RViz
leaves the simulation running. Ctrl+C in terminal 1 stops the full bring-up.

## 3. Try both control modes

Open **terminal 2** at the repository root. The example speaks HTTP, so this shell
only needs Python. Run the first command to cross the doorway and confirm it reports
`succeeded`. The second command backs up for two seconds,
then sends an explicit stop.

```bash
client=data/ros2_sim/policy-demo/policy-client.json
python3 ros2_sim/examples/policy_client.py "$client" pose 3.2 1.5 0
sleep 1.1  # Give the previous client's one-second lease time to expire.
python3 ros2_sim/examples/policy_client.py "$client" velocity -0.12 0 2
```

Pose goals use `x y yaw` in metres and radians in the map frame. Velocity commands
use `linear angular duration` in m/s, rad/s, and seconds. The example prints the
starting pose, local hazard readings, camera status, and command feedback.
The velocity example prints its last motion status before sending the stop.

## Choose a robot and world

Edit the defaults at the top of
[bringup.launch.py](src/rescuetwin_sim/launch/bringup.launch.py) and rebuild the
workspace, or override them on the command line. Use `--show-args` to list all
options. Launch from the repository root, or set `project_root` to the checkout
path if you’re launching from another directory.

| Argument | Default | Purpose |
| --- | --- | --- |
| `robot_model` | `turtlebot3_burger` | Robot profile. The Burger is the supported model. |
| `model_dir` | Extracted TurtleBot3 package under `data/ros2_sim/robot/` | Use a different installation of the Burger assets. |
| `world` | `public` | Pick the two-room floor or use `private` for a prepared scan. |
| `floor_dir` | `data/ros2_sim/first-floor` | Source floor for `world:=private`. |
| `run_dir` | `data/ros2_sim/policy-demo` for public or `data/ros2_sim/policy-scan` for private | Generated world and policy client descriptor. |

To run two simulations at once, give each its own `run_dir`, `policy_port`,
`ros_domain_id`, and `gz_partition`.

### Use a private floor

If the scan has already been prepared, launch it directly:

```bash
ros2 launch rescuetwin_sim bringup.launch.py world:=private
```

For a different prepared scan, set `floor_dir` and use a fresh `run_dir`.
In terminal 2, set `client` to `policy-client.json` in the selected run directory
and choose a goal on that floor.

To prepare a new scan, use an [authorized local bundle](../docs/DATA.md) with a
textured OBJ and translation-only registration. Set `bundle` to the bundle path,
and `x`, `y`, and `yaw` to the reviewed spawn in metres and radians. Check that
the collision opening aligns with the visible doorway.

```bash
ros2 run rescuetwin_sim prepare_world.py --bundle "$bundle" \
  --output data/ros2_sim/first-floor --spawn "$x" "$y" "$yaw"
```

Keep the scan, its provenance, and generated worlds in ignored local data directories.

### Run without windows

Pass `headless:=true` to skip both windows. Use a virtual display to keep Gazebo’s
RGB-D sensor active on a machine without a desktop:

```bash
LIBGL_ALWAYS_SOFTWARE=1 EGL_PLATFORM=x11 xvfb-run -a \
  ros2 launch rescuetwin_sim bringup.launch.py headless:=true
```

### Try a hazard fixture

Prepare a public run with an ignition in grid cell `[13, 6]`, then select that run
with the same launcher:

```bash
ros2 run rescuetwin_sim prepare_policy_run.py --model-dir "$models" \
  --output data/ros2_sim/policy-hazard --ignition 13 6
ros2 launch rescuetwin_sim bringup.launch.py run_dir:=data/ros2_sim/policy-hazard
```

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
with zero for invalid pixels. Both arrays are row-major. The camera renders geometry,
while normalized heat and smoke readings come from the 2D hazard model. The API
exposes observations and static map data. Evaluator state stays on the operator side.
