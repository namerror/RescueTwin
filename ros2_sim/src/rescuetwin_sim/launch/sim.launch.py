import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    world = Path(LaunchConfiguration('world').perform(context)).resolve()
    models = Path(LaunchConfiguration('model_dir').perform(context)).resolve()
    gui = LaunchConfiguration('gui').perform(context)
    if not world.is_file():
        raise RuntimeError('world must point to a generated SDF file')
    if not (models / 'turtlebot3_burger/model.sdf').is_file() or not (
        models / 'turtlebot3_common/meshes'
    ).is_dir():
        raise RuntimeError('model_dir must contain the upstream Burger and shared models')
    resources = [str(world.parent), str(models)]
    if os.environ.get('GZ_SIM_RESOURCE_PATH'):
        resources.append(os.environ['GZ_SIM_RESOURCE_PATH'])
    args = ['gz', 'sim', '-r']
    if gui == 'false':
        args.append('-s')
    else:
        args.extend(['--render-engine-gui', LaunchConfiguration('render_engine').perform(context)])
    config = Path(get_package_share_directory('rescuetwin_sim')) / 'config/bridge.yaml'
    return [
        ExecuteProcess(
            cmd=[*args, str(world)],
            additional_env={
                'GZ_SIM_RESOURCE_PATH': os.pathsep.join(resources),
                'GZ_MESH_FORCE_ASSIMP': '1',
            },
            output='screen',
            on_exit=[Shutdown()],
        ),
        Node(
            package='ros_gz_bridge', executable='parameter_bridge',
            parameters=[{'config_file': str(config)}], output='screen',
        ),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('world', description='Path to the generated SDF world'),
        DeclareLaunchArgument('model_dir', description='Path to upstream TurtleBot3 models'),
        DeclareLaunchArgument('gui', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('render_engine', default_value='ogre', choices=['ogre', 'ogre2']),
        OpaqueFunction(function=start),
    ])
