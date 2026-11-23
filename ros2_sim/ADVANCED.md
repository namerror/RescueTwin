# Gazebo command-line reference

For everyday use, start with the [visual setup](README.md).

## Manual workspace setup

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

## Launch directly with ROS

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

## Try both control modes

Open **terminal 2** at the repository root. The example speaks HTTP, so this shell
only needs Python. Run the first command to cross the doorway and confirm it reports
`succeeded`. The second command backs up for two seconds,
then sends an explicit stop.

```bash
python3 ros2_sim/examples/policy_client.py --client data/ros2_sim/policy-demo/policy-client.json pose 3.2 1.5 0
python3 ros2_sim/examples/policy_client.py --client data/ros2_sim/policy-demo/policy-client.json velocity -0.12 0 2
```

For a custom Python policy, import `Client` from `ros2_sim.examples.policy_client`
with the repository on your Python import path, and call
`Client.from_descriptor('data/ros2_sim/policy-demo/policy-client.json')`.
Use `Client.from_descriptor()` for a run started through `./setup.sh --gazebo`.

Pose goals use `x y yaw` in metres and radians in the map frame. Velocity commands
use `linear angular duration` in m/s, rad/s, and seconds. The example prints the
starting pose, local hazard readings, camera status, and command feedback.
The velocity example prints its last motion status before sending the stop.

## Choose a robot and world

Pass overrides to [bringup.launch.py](src/rescuetwin_sim/launch/bringup.launch.py).
Use `--show-args` to list all options. Launch from the repository root, or set `project_root` to the checkout
path if you’re launching from another directory.

| Argument | Default | Purpose |
| --- | --- | --- |
| `robot_model` | `turtlebot3_burger` | Robot profile. The Burger is the supported model. |
| `model_dir` | Extracted TurtleBot3 package under `data/ros2_sim/robot/` | Use a different installation of the Burger assets. |
| `world` | `public` | Pick the two-room floor or use `private` for a prepared scan. |
| `floor_dir` | `data/ros2_sim/first-floor` | Source floor for `world:=private`. |
| `run_dir` | `data/ros2_sim/policy-demo` for public, `data/ros2_sim/public-hazards` for the hazard demo, or `data/ros2_sim/policy-scan` for private | Generated world and policy client descriptor. |
| `hazard_demo` | `false` | Start the public floor with a seeded fire visible through the doorway. |

To run two simulations at once, give each its own `run_dir`, `policy_port`,
`ros_domain_id`, and `gz_partition`.

### Use a private floor

If the scan has already been prepared, launch it directly:

```bash
ros2 launch rescuetwin_sim bringup.launch.py world:=private
```

For a different prepared scan, set `floor_dir` and use a fresh `run_dir`.
In terminal 2, pass `--client` followed by the descriptor path printed by the
launcher and choose a goal on that floor.

New policy runs add missing OBJ normals to a local render copy so Ogre 2 can
display the scan textures. If an existing run shows a white scan, use a fresh
`run_dir` with the same floor and ignition settings. The setup screen prepares
a fresh run each time you launch.

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

Install `xvfb` and `xauth` for a machine without a desktop. The visual setup screen
can still be used when Gazebo and RViz windows are disabled:

```bash
LIBGL_ALWAYS_SOFTWARE=1 __EGL_VENDOR_LIBRARY_FILENAMES=/dev/null xvfb-run -a \
  ./setup.sh --gazebo --headless --no-browser
```

For direct ROS launch, pass `headless:=true` to skip both windows. A virtual display
keeps Gazebo’s RGB-D sensor active:

```bash
LIBGL_ALWAYS_SOFTWARE=1 EGL_PLATFORM=x11 xvfb-run -a \
  ros2 launch rescuetwin_sim bringup.launch.py headless:=true
```

If Ogre2 crashes in Mesa's EGL initialization under Xvfb, use X11/GLX for that
software-rendered session:

```bash
LIBGL_ALWAYS_SOFTWARE=1 __EGL_VENDOR_LIBRARY_FILENAMES=/dev/null xvfb-run -a \
  ros2 launch rescuetwin_sim bringup.launch.py headless:=true hazard_demo:=true
```

### Run the public fire and smoke demo

```bash
ros2 launch rescuetwin_sim bringup.launch.py hazard_demo:=true
```

The fire starts at grid cell `[13, 6]` and is visible from the starting pose in
Gazebo and in RViz's robot RGB view. Leave the robot stationary to watch flames
spread, smoke rise, and thicker smoke gather overhead and mix down. Pause with
Gazebo's world controls. A simulation clock reset restores the original field.
Particles already on screen can fade for up to 1.2 seconds of render time.
The standard launch remains clear-air.

For custom ignition cells, prepare a fresh run and pass its directory to bring-up.
Repeat `--ignition COL ROW` for multiple sources and use `--seed` to change the
propagation pattern. For a private floor, add `--floor "$floor_dir"` during
preparation and use `world:=private` with the same floor directory in bring-up.
Regenerate older ignition runs into a fresh directory to get the particle assets.

```bash
ros2 run rescuetwin_sim prepare_policy_run.py --model-dir "$models" \
  --output data/ros2_sim/custom-hazard --ignition 13 6 --seed 7
ros2 launch rescuetwin_sim bringup.launch.py run_dir:=data/ros2_sim/custom-hazard
```
