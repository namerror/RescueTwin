#!/usr/bin/env python3
"""Prepare a local Gazebo world from a validated, registered OBJ bundle."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET


def element(parent, tag, value=None, **attributes):
    child = ET.SubElement(parent, tag, attributes)
    if value is not None:
        child.text = value if isinstance(value, str) else ' '.join(map(str, value))
    return child


def box(link, name, position, size):
    collision = element(link, 'collision', name=name)
    element(collision, 'pose', (*position, 0, 0, 0))
    geometry = element(collision, 'geometry')
    element(element(geometry, 'box'), 'size', size)


def blocked_runs(blocked):
    rows = {}
    for col, row in blocked:
        rows.setdefault(row, []).append(col)
    for row, columns in sorted(rows.items()):
        columns.sort()
        first = last = columns[0]
        for col in columns[1:]:
            if col != last + 1:
                yield row, first, last
                first = col
            last = col
        yield row, first, last


def add_gui(world, spawn):
    x, y, yaw = spawn
    gui = element(world, 'gui')
    view = element(gui, 'plugin', filename='MinimalScene', name='3D View')
    element(view, 'scene', 'scene')
    element(view, 'camera_pose', (x - 0.24 * math.cos(yaw), y - 0.24 * math.sin(yaw),
                                  0.85, 0, 0.27, yaw))
    for plugin in ('GzSceneManager', 'InteractiveViewControl', 'WorldControl', 'EntityTree'):
        element(gui, 'plugin', filename=plugin, name=plugin)


def copy_material(source, destination):
    # MatterPak can declare just map_Kd. Assimp needs a neutral diffuse color
    # to display those baked textures instead of multiplying them by black.
    defaults = {'Ka': '1 1 1', 'Kd': '1 1 1', 'Ks': '0 0 0', 'illum': '1'}
    lines = source.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith('newmtl ')]
    for start, end in reversed(list(zip(starts, starts[1:] + [len(lines)]))):
        properties = {line.split()[0] for line in lines[start + 1:end] if line.strip()}
        lines[start + 1:start + 1] = [f'{key} {value}' for key, value in defaults.items()
                                    if key not in properties]
    destination.write_text('\n'.join(lines) + '\n')


def validate_floor(scene, spawn):
    mesh = scene['mesh']
    if (scene['schema'] != 'rescuetwin.scene/1' or mesh['format'] != 'obj'
            or not mesh['textured'] or not mesh['validation']['passed']
            or not scene['checks']['passed'] or not scene['exits']):
        raise ValueError('expected a validated textured OBJ scene with a reviewed exit')
    frame = scene['frame']
    if frame['units'] != 'm' or frame['up'] != '+z':
        raise ValueError('expected a metre-scale Z-up scene')
    transform = mesh['transform']
    if ([row[:3] for row in transform[:3]] != [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            or transform[3:] != [[0, 0, 0, 1]]):
        raise ValueError('expected a translation-only mesh registration')
    offset = [row[3] for row in transform[:3]]

    grid = scene['grid']
    ox, oy = grid['origin']
    res, width, height = grid['resolution'], grid['width'], grid['height']
    x, y, yaw = spawn
    if not all(map(math.isfinite, (x, y, yaw, ox, oy, res, *offset))) or min(res, width, height) <= 0:
        raise ValueError('invalid grid, registration, or spawn')
    blocked = {tuple(cell) for cells in scene['blocked'].values() for cell in cells}
    cell = (math.floor((x - ox) / res), math.floor((y - oy) / res))
    if not (0 <= cell[0] < width and 0 <= cell[1] < height) or cell in blocked:
        raise ValueError('spawn must be on the prepared free floor')
    return offset, blocked, cell


def build_world(scene, spawn):
    offset, blocked, _ = validate_floor(scene, spawn)
    mesh = scene['mesh']
    x, y, yaw = spawn
    grid = scene['grid']
    ox, oy = grid['origin']
    res, width, height = grid['resolution'], grid['width'], grid['height']

    sdf = ET.Element('sdf', version='1.8')
    world = element(sdf, 'world', name='rescuetwin')
    add_gui(world, spawn)
    for system in ('Physics', 'SceneBroadcaster'):
        filename = 'gz-sim-physics-system' if system == 'Physics' else 'gz-sim-scene-broadcaster-system'
        element(world, 'plugin', filename=filename, name=f'gz::sim::systems::{system}')
    scene_light = element(world, 'scene')
    element(scene_light, 'ambient', (0.75, 0.75, 0.75, 1))
    light = element(world, 'light', name='daylight', type='directional')
    element(light, 'direction', (-0.25, 0.2, -1))
    element(light, 'diffuse', (0.45, 0.45, 0.45, 1))
    model = element(world, 'model', name='scan_floor')
    element(model, 'static', 'true')
    link = element(model, 'link', name='structure')
    visual = element(link, 'visual', name='scan')
    element(visual, 'pose', (*offset, 0, 0, 0))
    geometry = element(visual, 'geometry')
    element(element(geometry, 'mesh'), 'uri', f"scan/{mesh['file']}")
    box(link, 'floor', (ox + width * res / 2, oy + height * res / 2, -0.05),
        (width * res, height * res, 0.1))
    for row, first, last in blocked_runs(blocked):
        box(link, f'blocked_{row}_{first}', (ox + (first + last + 1) * res / 2,
            oy + (row + 0.5) * res, 0.3), ((last - first + 1) * res, res, 0.6))

    robot = element(world, 'include')
    element(robot, 'uri', 'model://turtlebot3_burger')
    element(robot, 'pose', (x, y, 0.01, 0, 0, yaw))
    ET.indent(sdf)
    return ET.ElementTree(sdf)


def prepare(bundle, output, spawn):
    bundle = bundle.resolve()
    scene = json.loads((bundle / 'scene.json').read_text())
    world = build_world(scene, spawn)
    mesh = scene['mesh']
    assets = scene['assets']
    if not {mesh['file'], *mesh['materials']}.issubset(assets):
        raise ValueError('bundle is missing declared mesh or material files')
    if output.exists():
        raise ValueError('output directory already exists, choose a new directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / 'world'
        scan = staged / 'scan'
        scan.mkdir(parents=True)
        for name, info in assets.items():
            if name != Path(name).name or name in ('', '.', '..'):
                raise ValueError('bundle assets must use flat filenames')
            source = (bundle / name).resolve()
            if not source.is_relative_to(bundle) or not source.is_file():
                raise ValueError('bundle asset is missing or outside its directory')
            with source.open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != info['sha256']:
                    raise ValueError('bundle asset hash mismatch')
            if name in mesh['materials']:
                copy_material(source, scan / name)
            else:
                shutil.copyfile(source, scan / name)
        world.write(staged / 'world.sdf', encoding='utf-8', xml_declaration=True)
        (staged / 'source.json').write_text(json.dumps({
            'bundle': str(bundle), 'scene_id': scene['id'], 'spawn_xy_yaw': spawn,
        }, indent=2) + '\n')
        staged.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--spawn', required=True, type=float, nargs=3, metavar=('X', 'Y', 'YAW'))
    args = parser.parse_args()
    try:
        prepare(args.bundle, args.output, args.spawn)
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        # Avoid exposing private filenames, manifests, or scene IDs in command output.
        parser.exit(1, 'World preparation failed. Check bundle validation, assets, spawn, and output directory.\n')


if __name__ == '__main__':
    main()
