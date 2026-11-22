#!/usr/bin/env python3
"""Create a Gazebo policy run from the public test floor or a prepared local floor."""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from prepare_world import build_world, element


def synthetic():
    width, height = 20, 12
    blocked = [[x, y] for y in range(height) for x in range(width)
               if x in (0, width - 1) or y in (0, height - 1)
               or (x == 9 and y not in (5, 6))]
    scene = dict(schema='rescuetwin.scene/1', checks={'passed': True},
                 frame={'units': 'm', 'up': '+z'},
                 mesh=dict(format='obj', textured=True, validation={'passed': True},
                           file='unused.obj', transform=[[1, 0, 0, 0], [0, 1, 0, 0],
                                                         [0, 0, 1, 0], [0, 0, 0, 1]]),
                 grid=dict(width=width, height=height, origin=[0., 0.], resolution=.25),
                 blocked={'wall': blocked}, exits=[{'cell': [1, 1]}])
    world = build_world(scene, (1.55, 1.5, 0.))
    structure = world.find('world/model/link')
    structure.remove(structure.find('visual'))
    for collision in structure.findall('collision'):
        name = collision.attrib['name']
        visual = element(structure, 'visual', name=f'visual_{name}')
        visual.extend(deepcopy(list(collision)))
        material = element(visual, 'material')
        rgb = (.30, .36, .41) if name == 'floor' else (.64, .77, .72)
        element(material, 'ambient', (*rgb, 1))
        element(material, 'diffuse', (*rgb, 1))
        if name != 'floor':
            # Extend rendered walls above the camera while keeping the same planar footprint.
            pose = visual.find('pose').text.split()
            size = visual.find('geometry/box/size').text.split()
            pose[2], size[2] = '1.2', '2.4'
            visual.find('pose').text = ' '.join(pose)
            visual.find('geometry/box/size').text = ' '.join(size)
    return scene, world


def instrument_robot(models, target):
    target.mkdir(parents=True)
    source = models / 'turtlebot3_burger/model.sdf'
    tree = ET.parse(source)
    model = tree.find('model')
    link = model.find("link[@name='base_link']")
    scan = model.find("link[@name='base_scan']")
    for sensor in scan.findall('sensor'):
        scan.remove(sensor)
    drive = model.find("plugin[@name='gz::sim::systems::DiffDrive']")
    drive.find('topic').text = '/rescuetwin/guarded_velocity'
    element(drive, 'min_linear_acceleration', '-0.5')
    element(drive, 'max_angular_acceleration', '3.0')
    element(drive, 'min_angular_acceleration', '-3.0')
    localization = element(model, 'plugin', filename='gz-sim-odometry-publisher-system',
                           name='gz::sim::systems::OdometryPublisher')
    element(localization, 'odom_frame', 'map')
    element(localization, 'odom_topic', '/rescuetwin/internal_pose')
    element(localization, 'odom_publish_frequency', '30')
    sensor = element(link, 'sensor', name='policy_rgbd', type='rgbd_camera')
    element(sensor, 'pose', (.075, 0, .19, 0, 0, 0))
    element(sensor, 'topic', '/rescuetwin/rgbd')
    element(sensor, 'always_on', 'true')
    element(sensor, 'update_rate', '8')
    camera = element(sensor, 'camera')
    element(camera, 'horizontal_fov', '1.047')
    image = element(camera, 'image')
    element(image, 'width', '320')
    element(image, 'height', '240')
    clip = element(camera, 'clip')
    element(clip, 'near', '.08')
    element(clip, 'far', '8')
    tree.write(target / 'model.sdf')
    (target / 'model.config').write_bytes((source.parent / 'model.config').read_bytes())


def prepare(output, models, floor, ignitions, seed):
    if output.exists():
        raise ValueError('choose a fresh output directory')
    if floor is None:
        scene, tree = synthetic()
    else:
        provenance = json.loads((floor / 'source.json').read_text())
        bundle = Path(provenance['bundle'])
        scene = json.loads((bundle / 'scene.json').read_text())
        # Generate map and physics from the same input rather than relying on a stale SDF.
        tree = build_world(scene, provenance['spawn_xy_yaw'])
    world = tree.find('world')
    sensor_system = element(world, 'plugin', filename='gz-sim-sensors-system',
                            name='gz::sim::systems::Sensors')
    element(sensor_system, 'render_engine', 'ogre')
    grid = scene['grid']
    blocked = {tuple(cell) for group in scene['blocked'].values() for cell in group}
    if any(tuple(p) in blocked or not (0 <= p[0] < grid['width'] and 0 <= p[1] < grid['height'])
           for p in ignitions):
        raise ValueError('ignitions must be on free map cells')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / 'run'
        instrument_robot(models, staged / 'models/turtlebot3_burger')
        if floor is not None:
            (staged / 'scan').symlink_to((floor / 'scan').resolve(), target_is_directory=True)
        ET.indent(tree)
        tree.write(staged / 'world.sdf')
        (staged / 'policy.json').write_text(json.dumps(dict(
            map=dict(width=grid['width'], height=grid['height'], resolution=grid['resolution'],
                     origin=grid['origin'], blocked=sorted(blocked), frame='map'),
            exit_cell=scene['exits'][0]['cell'], seed=seed, ignitions=ignitions)))
        staged.rename(output)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--floor', type=Path, help='Prepared local floor. Omit for the synthetic fixture.')
    parser.add_argument('--ignition', nargs=2, type=int, action='append', default=[])
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    prepare(args.output, args.model_dir, args.floor, args.ignition, args.seed)


if __name__ == '__main__':
    main()
