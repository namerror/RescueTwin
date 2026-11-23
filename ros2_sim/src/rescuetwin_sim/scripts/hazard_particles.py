"""Generate the local particle textures and the bounded Gazebo emitter pool."""

import math
import struct
import zlib

from prepare_world import element


def write_texture(path, pixel, size=96):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel((x + .5) / size, (y + .5) / size))
    path.write_bytes(b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR', struct.pack('>2I5B', size, size, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def smooth(value):
    value = max(0., min(1., value))
    return value * value * (3. - 2. * value)


def noise(x, y):
    def lattice(col, row):
        value = math.sin(col * 127.1 + row * 311.7) * 43758.5453
        return value - math.floor(value)

    col, row = math.floor(x), math.floor(y)
    u, v = smooth(x - col), smooth(y - row)
    top = lattice(col, row) * (1. - u) + lattice(col + 1, row) * u
    bottom = lattice(col, row + 1) * (1. - u) + lattice(col + 1, row + 1) * u
    return top * (1. - v) + bottom * v


def flame(u, v, core):
    sway = .1 * math.sin(9. * v) + .04 * math.sin(21. * v)
    width = (.30 if core else .41) * v**.65
    edge = abs(u - .5 + sway * (1. - v))
    opacity = smooth((width - edge) / max(.03, .60 * width))
    opacity *= smooth(v / .29) * smooth((1. - v) / .33)
    detail = noise(8. * u, 6. * v)
    opacity *= .82 + .18 * detail
    if core:
        return 255, int(163 + 76 * v + 12 * detail), int(22 + 142 * v), int(242 * opacity)
    return 255, int(33 + 115 * v + 18 * detail), int(8 + 22 * v), int(227 * opacity)


def smoke(u, v):
    x, y = 2. * u - 1., 2. * v - 1.
    detail = (.47 * noise(3.4 * x + 19., 3.4 * y) + .17 * noise(8.5 * x, 8.5 * y + 13.) - .30)
    radius = math.hypot(x, y)
    alpha = smooth((.96 - radius + detail) / .75)
    shade = int(88 + 117 * (detail + .26))
    return shade, shade, shade + 4, int(172 * alpha)


def write_assets(directory):
    directory.mkdir()
    write_texture(directory / 'flame.png', lambda u, v: flame(u, v, False))
    write_texture(directory / 'core.png', lambda u, v: flame(u, v, True))
    write_texture(directory / 'smoke.png', smoke)


def add_emitters(world, grid, ignitions):
    element(world, 'plugin', filename='gz-sim-particle-emitter-system',
            name='gz::sim::systems::ParticleEmitter')
    model = element(world, 'model', name='rescuetwin_hazard_effects')
    element(model, 'static', 'true')
    link = element(model, 'link', name='emitters')
    resolution = grid['resolution']
    stride = max(1, math.ceil(.50 / resolution))
    initial = {}
    for x, y in sorted(ignitions, key=lambda cell: (cell[1] // stride, cell[0] // stride,
                                                     cell[1], cell[0])):
        initial.setdefault((y // stride, x // stride),
                           (grid['origin'][0] + (x + .5) * resolution,
                            grid['origin'][1] + (y + .5) * resolution))
    initial = list(initial.values())
    # The order and count match the publisher's fixed slots.
    effects = [('flame', 12, .65, .65, 1.3, 0., .025),
               ('core', 12, .40, .45, .85, 0., .025),
               ('plume', 8, 1.05, .75, 1.25, .2, .05),
               ('ceiling', 12, 1.2, .03, .12, .1, .05),
               ('low', 12, 1.2, .03, .12, .1, .05)]
    for effect, count, lifetime, minimum, maximum, growth, scatter in effects:
        for index in range(count):
            name = f'{effect}_{index}'
            emitter = element(link, 'particle_emitter', name=name, type='box')
            element(emitter, 'topic', f'/rescuetwin/hazard_effects/{name}')
            if effect in ('flame', 'core') and index < len(initial):
                x, y = initial[index]
                width, height, z, rate = ((.37, .93, .11, 31) if effect == 'flame'
                                          else (.21, .54, .10, 23))
                spread = min(resolution * .75, width * .7)
                element(emitter, 'pose', (x, y, z, 0, -math.pi / 2, 0))
                element(emitter, 'size', (.04, spread, spread))
                element(emitter, 'particle_size', (width, height, width))
                element(emitter, 'rate', (rate,))
                element(emitter, 'emitting', 'true')
            else:
                element(emitter, 'pose', (0, 0, -2, 0, -math.pi / 2, 0))
                element(emitter, 'emitting', 'false')
            element(emitter, 'lifetime', (lifetime,))
            element(emitter, 'min_velocity', (minimum,))
            element(emitter, 'max_velocity', (maximum,))
            element(emitter, 'scale_rate', (growth,))
            element(emitter, 'particle_scatter_ratio', (scatter,))
            element(emitter, 'color_start', (1., 1., 1., 1.))
            element(emitter, 'color_end', (1., 1., 1., .12 if effect in ('flame', 'core') else .28))
            material = element(emitter, 'material')
            texture = effect if effect in ('flame', 'core') else 'smoke'
            metal = element(element(material, 'pbr'), 'metal')
            element(metal, 'albedo_map', f'hazard_textures/{texture}.png')
            element(material, 'diffuse', (1, 1, 1, 1))
