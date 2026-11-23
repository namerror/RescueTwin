"""Regression checks for the shared field and Gazebo particle placement."""

import numpy as np

from rescuetwin.core import Scene
from rescuetwin.gazebo_hazards import ParticleLayout
from rescuetwin.hazards import HazardField


def test_smoke_crosses_the_opening_ceiling_first():
    scene = Scene(width=15, height=9, resolution=.25, exits=[(1, 1)],
                  walls={(7, y) for y in range(9) if y != 4})
    sealed = Scene(width=15, height=9, resolution=.25, exits=[(1, 1)],
                   walls=scene.walls | {(7, 4)})
    a = HazardField(scene, [(5, 4)], 7)
    b = HazardField(sealed, [(5, 4)], 7)
    clear = HazardField(scene, [], 7)
    for _ in range(30):
        for field in (a, b, clear):
            field.advance(.2)
    assert a.upper_smoke[4, 9] > a.smoke[4, 9] > 0
    assert not b.upper_smoke[:, 8:].any()
    assert not b.smoke[:, 8:].any()
    assert not a.upper_smoke[~a.free].any()
    assert not clear.upper_smoke.any() and not clear.smoke.any() and not clear.fire.any()


def test_ignition_and_particle_layout_use_metric_free_cells_and_fixed_capacity():
    scene = Scene(width=10, height=9, resolution=.25, origin=(-2., 3.),
                  exits=[(1, 1)], walls={(5, 4)})
    field = HazardField(scene, [(4, 4)], 7)
    layout = ParticleLayout(scene, 2.4)
    first = list(layout.snapshot(field))
    assert len(first) == 56
    assert [(s.effect, s.x, s.y) for s in first if s.rate] == [
        ('flame', *scene.world((4, 4))), ('core', *scene.world((4, 4)))]
    assert list(layout.snapshot(field)) == first
    for _ in range(70):
        field.advance(.2)
    subsequent = list(layout.snapshot(field))
    assert any(s.effect == 'ceiling' and s.rate for s in subsequent)
    assert all(scene.free(scene.cell(s.x, s.y)) for s in subsequent if s.rate)
    assert not np.array_equal(field.threshold, HazardField(scene, [(4, 4)], 11).threshold)
