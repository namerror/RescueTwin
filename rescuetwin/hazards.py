"""Normalized single-floor fire and smoke heuristics on a metric grid."""

import math

import numpy as np

# Ignition temperature, relative burn rate, fuel load, smoke yield.
MATERIALS = {
    'inert': (1., 0., 0., 0.),
    'light': (.24, 1.05, .65, .48),
    'wood': (.31, .85, 1., .40),
    'upholstery': (.19, 1.25, 1.15, .72),
}

INITIAL_HEAT = .9
STARTER_SECONDS = 3.
PREHEAT_DOSE = .14


class HazardField:
    """Keep combustion at floor level and move smoke in two vertical bands."""

    def __init__(self, scene, ignitions, seed):
        self.resolution = scene.resolution
        self.free = scene.free_mask()
        self.update_floor(self.free)
        shape = self.free.shape
        self.heat = np.zeros(shape)
        self.fire = np.zeros(shape)
        self.smoke = np.zeros(shape)
        self.upper_smoke = np.zeros(shape)
        self.preheat = np.zeros(shape)
        self.burn_age = np.zeros(shape)
        self.burning = np.zeros(shape, dtype=bool)
        self.starter = np.zeros(shape)
        self.starter_strength = np.zeros(shape)
        profiles = np.empty((4, *shape))
        profiles[:] = np.array(MATERIALS[scene.default_material])[:, None, None]
        for (x, y), material in scene.materials.items():
            profiles[:, y, x] = MATERIALS[material]
        threshold, self.burn_rate, fuel, self.smoke_yield = profiles
        variation = np.random.default_rng(seed % (1 << 64)).uniform(.92, 1.08, shape)
        self.threshold = threshold * variation
        self.initial_fuel = fuel
        self.fuel = np.where(self.free, fuel, 0.)
        exchange = np.zeros(shape)
        for x, y in set(map(tuple, scene.exits)) | scene.vents:
            exchange[y, x] = 1.
        self.heat_loss = .045 + .16 * exchange
        self.smoke_loss = .012 + .24 * exchange
        self.ignitions = set()
        self.time = 0.
        self.ignite(ignitions)

    def update_floor(self, free):
        self.free[:] = free
        self.edges = (free[1:, :] & free[:-1, :], free[:, 1:] & free[:, :-1])

    def clear_cell(self, cell):
        x, y = cell
        for field in (self.heat, self.fire, self.smoke, self.upper_smoke, self.fuel,
                      self.preheat, self.burn_age, self.burning, self.starter, self.starter_strength):
            field[y, x] = 0
        self.ignitions.discard(cell)

    def ignite(self, cells, initial_smoke=0., sustained=True):
        for x, y in cells:
            if self.free[y, x]:
                self.ignitions.add((x, y))
                if sustained:
                    self.starter[y, x] = STARTER_SECONDS
                    self.starter_strength[y, x] = INITIAL_HEAT
                    self.burning[y, x] = self.fuel[y, x] > 0.
                self.fire[y, x] = self.heat[y, x] = INITIAL_HEAT
                self.smoke[y, x] = max(self.smoke[y, x], initial_smoke)
                self.upper_smoke[y, x] = max(self.upper_smoke[y, x], initial_smoke)

    def advance(self, seconds, cooling_rate=None):
        if seconds <= 0.:
            return
        # Bound each explicit transport step as grid resolution gets finer.
        heat_transfer = .040 / self.resolution**2
        lower_transfer = .023 / self.resolution**2
        upper_transfer = .065 / self.resolution**2
        steps = max(1, math.ceil(seconds * (4 * upper_transfer + .8) / .75))
        step = seconds / steps
        cooling = np.exp(-cooling_rate * step) if cooling_rate is not None else None
        for _ in range(steps):
            self.time += step
            self._step(step, heat_transfer, lower_transfer, upper_transfer, cooling)

    def _step(self, step, heat_transfer, lower_transfer, upper_transfer, cooling):
        if cooling is not None:
            self.heat *= cooling
            self.starter_strength *= cooling
            self.burning &= self.heat >= self.threshold
        starter = self.starter_strength * np.minimum(1., self.starter)
        self.starter[:] = np.maximum(0., self.starter - step)
        # Nearby flames preheat fuel along the same open edges as air transport.
        nearby_flame = self._strongest_neighbor(self.fire * self.burning)
        radiant_heat = .20 * min(1.6, self.resolution / .25) * nearby_flame
        self.preheat[:] = np.maximum(0., self.preheat + step * (
            self.heat + radiant_heat - self.threshold))
        self.burning |= (self.preheat >= PREHEAT_DOSE) & (self.fuel > 0.)
        self.burning &= self.fuel > 0.
        self.burn_age[:] = np.where(self.burning, self.burn_age + step, 0.)
        remaining = self.fuel / np.maximum(.001, self.initial_fuel)
        growth = np.clip(self.burn_age / 2., .22, 1.)
        flare = np.minimum(growth, np.sqrt(remaining))
        burn = np.minimum(self.fuel / step, .046 * self.burn_rate * np.maximum(.3, flare))
        burn *= self.burning
        self.fuel[:] = np.maximum(0., self.fuel - step * burn)
        intensity = self.burning * np.minimum(1., flare * self.burn_rate)
        self.fire[:] = np.maximum(starter, intensity)
        self.heat[:] = np.maximum(starter, np.clip(self.heat + step * (
            heat_transfer * self._laplacian(self.heat) + 1.65 * intensity * (1. - self.heat)
            - self.heat_loss * self.heat), 0., 1.))

        # Young smoke rises quickly. The ceiling layer transfers smoke down as it gets dense.
        rising = .36 * self.smoke
        mixing_down = (.065 + .65 * self.upper_smoke) * self.upper_smoke
        release = 16. * burn * self.smoke_yield
        lower_delta = (lower_transfer * self._laplacian(self.smoke) + .15 * release
                       + mixing_down - rising - self.smoke_loss * self.smoke)
        upper_delta = (upper_transfer * self._laplacian(self.upper_smoke) + .85 * release
                       + rising - mixing_down - self.smoke_loss * self.upper_smoke)
        self.smoke[:] = np.clip(self.smoke + step * lower_delta, 0., 1.)
        self.upper_smoke[:] = np.clip(self.upper_smoke + step * upper_delta, 0., 1.)

    def _laplacian(self, field):
        vertical, horizontal = self.edges
        flux_y = (field[:-1, :] - field[1:, :]) * vertical
        flux_x = (field[:, :-1] - field[:, 1:]) * horizontal
        delta = np.zeros_like(field)
        delta[1:, :] += flux_y
        delta[:-1, :] -= flux_y
        delta[:, 1:] += flux_x
        delta[:, :-1] -= flux_x
        return delta

    def _strongest_neighbor(self, field):
        vertical, horizontal = self.edges
        strongest = np.zeros_like(field)
        strongest[1:, :] = field[:-1, :] * vertical
        np.maximum(strongest[:-1, :], field[1:, :] * vertical, out=strongest[:-1, :])
        np.maximum(strongest[:, 1:], field[:, :-1] * horizontal, out=strongest[:, 1:])
        np.maximum(strongest[:, :-1], field[:, 1:] * horizontal, out=strongest[:, :-1])
        return strongest
