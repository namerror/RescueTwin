# RescueTwin Gazebo bring-up

This standalone ROS 2 workspace runs a TurtleBot3 Burger in one prepared RescueTwin floor.
It uses Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, and the installed `ros_gz` bridge.
Gazebo runs the stock differential drive and physics. Manual velocity commands are the
entry point for this milestone.

## Build and launch

Run from the project root in a Bash shell. On the machine used for bring-up, the
prepared world and extracted robot are already under `data/ros2_sim/`.

```bash
source /opt/ros/jazzy/setup.bash
(cd ros2_sim && /usr/bin/colcon build --packages-select rescuetwin_sim --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3)
source ros2_sim/install/local_setup.bash

export ROS_DOMAIN_ID=71 GZ_PARTITION=rescuetwin_bringup
export ROS_LOG_DIR="$PWD/ros2_sim/log/ros"
models="$PWD/data/ros2_sim/robot/opt/ros/jazzy/share/turtlebot3_gazebo/models"
world="$PWD/data/ros2_sim/first-floor/world.sdf"
ros2 launch rescuetwin_sim sim.launch.py world:="$world" model_dir:="$models"
```

The GUI opens at the doorway using the Ogre renderer. Append `gui:=false` for a
server-only run, or `render_engine:=ogre2` to select Ogre 2. Stop the launch with Ctrl+C.
Use the same ROS domain in a second terminal. Publish a command at 10 Hz and end
the publisher with Ctrl+C before sending the explicit zero command. The stock drive
can continue applying the last velocity after a publisher exits.

```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=71
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/TwistStamped '{header: auto, twist: {linear: {x: 0.12}}}'
ros2 topic pub --once /cmd_vel geometry_msgs/msg/TwistStamped '{header: auto, twist: {}}'
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/TwistStamped '{header: auto, twist: {angular: {z: 0.5}}}'
ros2 topic pub --once /cmd_vel geometry_msgs/msg/TwistStamped '{header: auto, twist: {}}'
ros2 topic echo /odom --once
```

Use a negative `linear.x` to reverse. Speed is in m/s and yaw rate is in rad/s.
The bridge publishes simulation time on `/clock` and `odom` → `base_footprint` on
`/tf`. Odometry reports wheel-derived motion. Use the simulator pose to evaluate
contact and wall blocking. From a terminal with the same `GZ_PARTITION`, run
`gz topic -e -n 1 -t /world/rescuetwin/pose/info --json-output` and inspect the
`turtlebot3_burger` pose. The prepared local world's `doorway.json` records the
opening used for the crossing check.

## Prepare local assets

On another Ubuntu 24.04 machine with the ROS Jazzy apt source, download and extract
the ROBOTIS package without installing it into the OS.

```bash
mkdir -p data/ros2_sim/robot
(
  cd data/ros2_sim/robot
  apt-get download ros-jazzy-turtlebot3-gazebo=2.3.7-1noble.20261105.142500
  dpkg-deb -x ros-jazzy-turtlebot3-gazebo_2.3.7-1noble.20261105.142500_*.deb .
)
```

For the scan, select an authorized, locally prepared RescueTwin bundle with a
validated textured OBJ, translation-only mesh registration, and a visibly open
interior doorway. Choose a spawn slightly before the opening, facing into it.
Use metres and radians in the bundle's Z-up frame. For an already prepared world,
the local `source.json` records the original bundle path and spawn.

```bash
ros2 run rescuetwin_sim prepare_world.py --bundle "$bundle" \
  --output data/ros2_sim/first-floor --spawn "$x" "$y" "$yaw"
SDF_PATH="$models" gz sdf -k data/ros2_sim/first-floor/world.sdf
python3 -m unittest discover -s ros2_sim/src/rescuetwin_sim/tests
```

Choose a fresh output directory when regenerating. The converter copies the verified
visual assets, supplies missing material defaults in its local copies, and builds a
floor slab and static colliders from the prepared grid.
Review the collider opening against the visible jambs before driving a new scene.
The grid is used for geometry only. The approximate colliders, generated scene,
and source provenance stay local under ignored `data/ros2_sim/`.
