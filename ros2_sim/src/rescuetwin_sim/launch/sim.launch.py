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
    policy_python = LaunchConfiguration('policy_python').perform(context)
    if policy_python:
        resources.insert(0, str(world.parent / 'models'))
        if not (world.parent / 'policy.json').is_file():
            raise RuntimeError('policy mode requires a prepared policy world')
    elif (world.parent / 'policy.json').is_file():
        raise RuntimeError('prepared policy worlds require policy_python')
    if os.environ.get('GZ_SIM_RESOURCE_PATH'):
        resources.append(os.environ['GZ_SIM_RESOURCE_PATH'])
    args = ['gz', 'sim', '-r']
    if gui == 'false':
        args.append('-s')
    else:
        args.extend(['--render-engine-gui', LaunchConfiguration('render_engine').perform(context)])
    config_dir = Path(get_package_share_directory('rescuetwin_sim')) / 'config'
    config = config_dir / ('policy_bridge.yaml' if policy_python else 'bridge.yaml')
    actions = [
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
            on_exit=[Shutdown()] if policy_python else None,
        ),
    ]
    if policy_python:
        actions.extend([
            Node(
                package='ros_gz_bridge', executable='parameter_bridge', name='rescuetwin_camera_bridge',
                parameters=[{'config_file': str(config_dir / 'camera_bridge.yaml'),
                             'override_frame_id': 'camera_optical'}],
                output='screen', on_exit=[Shutdown()],
            ),
            Node(
                package='tf2_ros', executable='static_transform_publisher',
                name='rescuetwin_camera_transform',
                # Both Burger base links share their origin. Optical axes are right, down, forward.
                arguments=['--x', '0.075', '--z', '0.19', '--qx', '-0.5', '--qy', '0.5',
                           '--qz', '-0.5', '--qw', '0.5', '--frame-id', 'base_footprint',
                           '--child-frame-id', 'camera_optical'],
                output='screen',
            ),
        ])
        actions.append(ExecuteProcess(
            cmd=[policy_python, '-m', 'rescuetwin.policy_runtime', '--config',
                 str(world.parent / 'policy.json'), '--port',
                 LaunchConfiguration('policy_port').perform(context)],
            output='screen', on_exit=[Shutdown()],
        ))
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('world', description='Path to the generated SDF world'),
        DeclareLaunchArgument('model_dir', description='Path to upstream TurtleBot3 models'),
        DeclareLaunchArgument('gui', default_value='true', choices=['true', 'false']),
        DeclareLaunchArgument('render_engine', default_value='ogre', choices=['ogre', 'ogre2']),
        DeclareLaunchArgument('policy_python', default_value='',
                              description='Path to Python with the ros-policy extra to enable the API'),
        DeclareLaunchArgument('policy_port', default_value='8091'),
        OpaqueFunction(function=start),
    ])
