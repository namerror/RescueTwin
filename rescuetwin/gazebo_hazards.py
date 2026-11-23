"""Select a bounded set of Gazebo particle sources from the operator hazard field."""

from dataclasses import dataclass
import math
from queue import Empty, Full, Queue
import subprocess
from threading import Thread

import numpy as np


@dataclass(frozen=True)
class ParticleSource:
    effect: str
    slot: int
    x: float = 0.
    y: float = 0.
    z: float = 0.
    spread: float = 0.
    width: float = 0.
    height: float = 0.
    rate: float = 0.

    def command(self):
        return (f'{self.effect} {self.slot} {self.x:.4f} {self.y:.4f} {self.z:.4f} '
                f'{self.spread:.4f} {self.width:.4f} {self.height:.4f} {self.rate:.2f}\n')


class ParticleLayout:
    def __init__(self, scene, ceiling_m):
        self.scene = scene
        self.ceiling_m = ceiling_m
        self.slots = {effect: [None] * count for effect, count in
                      [('flame', 12), ('core', 12), ('plume', 8), ('ceiling', 12), ('low', 12)]}

    def _patches(self, grid, spacing, threshold, count):
        # Use the strongest actual cell in each patch to keep sources on free floor.
        stride = max(1, math.ceil(spacing / self.scene.resolution))
        height, width = grid.shape
        padded = np.pad(grid, ((0, (-height) % stride), (0, (-width) % stride)))
        tiles = padded.reshape(padded.shape[0] // stride, stride, padded.shape[1] // stride, stride)
        tiles = tiles.transpose(0, 2, 1, 3).reshape(-1, stride * stride)
        peaks = tiles.max(axis=1)
        ranked = np.argsort(-peaks, kind='stable')[:count]
        columns = padded.shape[1] // stride
        selected = {}
        for tile in ranked:
            if peaks[tile] < threshold:
                break
            offset_y, offset_x = divmod(int(tiles[tile].argmax()), stride)
            row, col = divmod(int(tile), columns)
            selected[int(tile)] = ((col * stride + offset_x, row * stride + offset_y), peaks[tile])
        return selected

    def snapshot(self, field):
        flames = self._patches(field.fire, .50, .10, 12)
        plume = self._patches(field.fire * field.upper_smoke, .65, .017, 8)
        ceiling = self._patches(field.upper_smoke, .75, .045, 12)
        low = self._patches(field.smoke, .75, .055, 12)
        for effect, patches in [('flame', flames), ('core', flames), ('plume', plume),
                                ('ceiling', ceiling), ('low', low)]:
            slots = self.slots[effect]
            keep = set(patches)
            for slot, tile in enumerate(slots):
                if tile in keep:
                    keep.remove(tile)
                else:
                    slots[slot] = None
            available = (slot for slot, tile in enumerate(slots) if tile is None)
            for slot, tile in zip(available, (tile for tile in patches if tile in keep)):
                slots[slot] = tile
            for slot, tile in enumerate(slots):
                if tile is None:
                    yield ParticleSource(effect, slot)
                    continue
                cell, intensity = patches[tile]
                x, y = self.scene.world(cell)
                width, height, z, rate = {
                    'flame': (.21 + .18 * intensity, .55 + .42 * intensity, .11, 13 + 20 * intensity),
                    'core': (.10 + .12 * intensity, .30 + .27 * intensity, .10, 10 + 14 * intensity),
                    'plume': (.36, .44, min(.70, .36 * self.ceiling_m), 8 + 16 * intensity),
                    'ceiling': (.52, .34, .85 * self.ceiling_m, 5 + 28 * intensity),
                    'low': (.55, .43, .23 * self.ceiling_m, 5 + 29 * intensity),
                }[effect]
                spread = min(self.scene.resolution * .75, width * .7)
                yield ParticleSource(effect, slot, x, y, z, spread, width, height, rate)


class ParticlePublisher:
    """Send only the latest full snapshot, independently of the control timer."""

    def __init__(self, executable):
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, text=True)
        self.pending = Queue(maxsize=1)
        self.worker = Thread(target=self._write, daemon=True)
        self.worker.start()

    def submit(self, sources):
        if self.process.poll() is not None or not self.worker.is_alive():
            raise RuntimeError('Gazebo hazard particle publisher stopped')
        packet = ''.join(source.command() for source in sources)
        try:
            self.pending.put_nowait(packet)
        except Full:
            try:
                self.pending.get_nowait()
            except Empty:
                pass
            self.pending.put_nowait(packet)

    def _write(self):
        try:
            with self.process.stdin as stream:
                for packet in iter(self.pending.get, None):
                    stream.write(packet)
                    stream.flush()
        except (BrokenPipeError, OSError):
            return

    def close(self):
        try:
            self.pending.get_nowait()
        except Empty:
            pass
        self.pending.put_nowait(None)
        self.worker.join(timeout=1)
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait()
        self.worker.join(timeout=1)
