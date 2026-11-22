"""Start a RescueTwin policy simulation and its robot-camera viewer."""
import os
from pathlib import Path
import subprocess
import sys

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.actions import OpaqueFunction, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import EnvironmentVariable, LaunchConfiguration


# Paths here are relative to project_root. The ROS launch arguments can override them.
DEFAULT_ROBOT = 'turtlebot3_burger'
DEFAULT_WORLD = 'public'
WORLD_RUNS = {
    'public': 'data/ros2_sim/policy-demo',
    'private': 'data/ros2_sim/policy-scan',
}
DEFAULT_PRIVATE_FLOOR = 'data/ros2_sim/first-floor'
DEFAULT_MODEL_DIR = 'data/ros2_sim/robot/opt/ros/jazzy/share/turtlebot3_gazebo/models'


def prepare_run(run, models, robot, floor):
    if not run.exists():
        generator = (Path(get_package_prefix('rescuetwin_sim'))
                     / 'lib/rescuetwin_sim/prepare_policy_run.py')
        command = [sys.executable, str(generator), '--model-dir', str(models), '--output', str(run)]
        if floor is not None:
            command.extend(['--floor', str(floor)])
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                'World preparation failed. Check the selected floor and run directory.'
            ) from error

    required = ('world.sdf', 'policy.json', f'models/{robot}/model.sdf',
                f'models/{robot}/model.config')
    if not all((run / name).is_file() for name in required):
        raise RuntimeError(f'Incomplete policy run at {run}. Choose a fresh run_dir.')

    scan = run / 'scan'
    if floor is None:
        matches = not scan.exists() and not scan.is_symlink()
    else:
        matches = scan.is_symlink() and scan.resolve() == (floor / 'scan').resolve()
    if not matches:
        raise RuntimeError(f'The selected world or floor differs from the run at {run}. Choose a fresh run_dir.')

    if (run / 'policy-client.json').exists():
        raise RuntimeError(f'A policy client descriptor already exists at {run}. '
                           'Stop the existing bring-up or choose a fresh run_dir.')


def start(context):
    def argument(name):
        return LaunchConfiguration(name).perform(context)

    root = Path(argument('project_root')).expanduser().resolve()

    def project_path(name, default):
        return (root / Path(argument(name) or default).expanduser()).resolve()

    robot, world = argument('robot_model'), argument('world')
    models = project_path('model_dir', DEFAULT_MODEL_DIR)
    model = models / robot
    if (not (model / 'model.sdf').is_file() or not (model / 'model.config').is_file()
            or not (models / 'turtlebot3_common/meshes').is_dir()):
        raise RuntimeError(f'TurtleBot3 assets are missing at {models}. '
                           'Extract the Jazzy TurtleBot3 Gazebo package or set model_dir.')

    # Keep the venv's bin/python path even when the executable is a symlink.
    policy_python = root / Path(argument('policy_python')).expanduser()
    if not policy_python.is_file() or not os.access(policy_python, os.X_OK):
        raise RuntimeError('Policy Python is missing. Install the ros-policy extra or set policy_python.')

    headless = argument('headless') == 'true'
    if not (os.environ.get('DISPLAY') or (not headless and os.environ.get('WAYLAND_DISPLAY'))):
        raise RuntimeError('Start in a desktop terminal, or run through xvfb-run with headless:=true.')

    run = project_path('run_dir', WORLD_RUNS[world])
    floor = None
    if world == 'private':
        floor = project_path('floor_dir', DEFAULT_PRIVATE_FLOOR)
        if not (floor / 'source.json').is_file() or not (floor / 'scan').is_dir():
            raise RuntimeError(f'Prepared scan required at {floor}. Run prepare_world.py or set floor_dir.')
    prepared = run.exists()
    prepare_run(run, models, robot, floor)

    launch_dir = Path(get_package_share_directory('rescuetwin_sim')) / 'launch'
    sim_args = {
        'world': str(run / 'world.sdf'),
        'model_dir': str(models),
        'gui': 'false' if headless else 'true',
        'policy_python': str(policy_python),
        'policy_port': argument('policy_port'),
    }
    actions = [
        LogInfo(msg=f'{"Using" if prepared else "Prepared"} {world} world with {robot} at {run}'),
        LogInfo(msg=f'Policy client descriptor: {run / "policy-client.json"}'),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(launch_dir / 'sim.launch.py')),
            launch_arguments=sim_args.items(),
        ),
    ]
    if not headless:
        actions.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(launch_dir / 'robot_camera.launch.py')),
        ))
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('project_root', default_value=os.getcwd(),
                              description='RescueTwin checkout used to resolve relative paths'),
        DeclareLaunchArgument('robot_model', default_value=DEFAULT_ROBOT,
                              choices=['turtlebot3_burger'], description='Supported robot profile'),
        DeclareLaunchArgument('world', default_value=DEFAULT_WORLD, choices=list(WORLD_RUNS),
                              description='Public two-room world or private prepared scan'),
        DeclareLaunchArgument('model_dir', default_value=DEFAULT_MODEL_DIR,
                              description='Extracted TurtleBot3 Gazebo models directory'),
        DeclareLaunchArgument('floor_dir', default_value=DEFAULT_PRIVATE_FLOOR,
                              description='Prepared scan directory for world:=private'),
        DeclareLaunchArgument('run_dir', default_value='',
                              description='Prepared policy output directory, defaults to policy-demo or policy-scan'),
        DeclareLaunchArgument('headless', default_value='false', choices=['true', 'false'],
                              description='Disable both windows while keeping the RGB-D camera active'),
        DeclareLaunchArgument('policy_python', default_value='.venv/bin/python',
                              description='Python executable with the ros-policy extra'),
        DeclareLaunchArgument('policy_port', default_value='8091',
                              description='Loopback port for the policy API'),
        DeclareLaunchArgument('ros_domain_id',
                              default_value=EnvironmentVariable('ROS_DOMAIN_ID', default_value='71'),
                              description='ROS domain shared by Gazebo bridges and RViz'),
        DeclareLaunchArgument('gz_partition',
                              default_value=EnvironmentVariable('GZ_PARTITION', default_value='rescuetwin_policy_demo'),
                              description='Gazebo Transport partition for this simulation'),
        SetEnvironmentVariable('ROS_DOMAIN_ID', LaunchConfiguration('ros_domain_id')),
        SetEnvironmentVariable('GZ_PARTITION', LaunchConfiguration('gz_partition')),
        OpaqueFunction(function=start),
    ])
