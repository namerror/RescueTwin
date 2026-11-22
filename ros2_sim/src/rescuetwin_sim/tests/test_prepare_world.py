import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location(
    'prepare_world', Path(__file__).resolve().parents[1] / 'scripts/prepare_world.py',
)
prepare_world = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_world)


class PrepareWorldTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        assets = {
            'floor.obj': b'mtllib floor.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\n'
                         b'vt 1 0\nvt 0 1\nusemtl paint\nf 1/1 2/2 3/3\n',
            'floor.mtl': b'newmtl paint\nKd 0.7 0.7 0.7\n',
        }
        for name, content in assets.items():
            (self.bundle / name).write_bytes(content)
        manifest = {
            'schema': 'rescuetwin.scene/1', 'id': 'synthetic',
            'checks': {'passed': True}, 'exits': [{'cell': [2, 1]}],
            'frame': {'units': 'm', 'up': '+z'},
            'grid': {'origin': [1, 2], 'resolution': 0.25, 'width': 4, 'height': 4},
            'blocked': {'wall': [[0, 0], [1, 0], [3, 0]]},
            'mesh': {
                'format': 'obj', 'textured': True, 'validation': {'passed': True},
                'file': 'floor.obj', 'materials': ['floor.mtl'],
                'transform': [[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, -0.2], [0, 0, 0, 1]],
            },
            'assets': {name: {'sha256': hashlib.sha256(content).hexdigest()}
                       for name, content in assets.items()},
        }
        (self.bundle / 'scene.json').write_text(json.dumps(manifest))
        self.output = self.root / 'sim'

    def test_registers_visual_and_leaves_doorway_open(self):
        prepare_world.prepare(self.bundle, self.output, (1.625, 2.375, 0))
        root = ET.parse(self.output / 'world.sdf')
        origin = root.find('world/model/link/visual/pose').text
        self.assertEqual(origin, '1 2 -0.2 0 0 0')
        uri = root.find('world/model/link/visual/geometry/mesh/uri').text
        self.assertTrue((self.output / uri).is_file())
        material = (self.output / 'scan/floor.mtl').read_text()
        self.assertIn('Ka 1 1 1', material)
        self.assertIn('Kd 0.7 0.7 0.7', material)
        self.assertNotIn('Kd 1 1 1', material)
        collisions = root.findall('world/model/link/collision')
        self.assertEqual([item.attrib['name'] for item in collisions],
                         ['floor', 'blocked_0_0', 'blocked_0_3'])
        self.assertEqual(collisions[1].find('geometry/box/size').text, '0.5 0.25 0.6')

    def test_rejects_blocked_spawn_and_modified_assets(self):
        with self.assertRaisesRegex(ValueError, 'spawn'):
            prepare_world.prepare(self.bundle, self.output, (1.125, 2.125, 0))
        (self.bundle / 'floor.mtl').write_text('modified')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            prepare_world.prepare(self.bundle, self.output, (1.625, 2.375, 0))
        self.assertFalse(self.output.exists())
