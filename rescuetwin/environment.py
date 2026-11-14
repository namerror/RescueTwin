"""Convert an annotated metric floor plan into a simulation scene."""

from collections import deque
import json
import math
from pathlib import Path

from .core import MATERIALS, Scene


def inside(x, y, box):
    return box[0] <= x < box[1] and box[2] <= y < box[3]


def bounds(values, size, label):
    if len(values) != size or not all(
        type(v) in (int, float) and math.isfinite(v) for v in values
    ):
        raise ValueError(f"{label} needs {size} finite metric coordinates")
    return values


def rectangles(items, size, label):
    for item in items:
        box = bounds(item, size, label)
        if any(lo >= hi for lo, hi in zip(box[::2], box[1::2])):
            raise ValueError(f"{label} must have increasing bounds")
        yield box


def load_environment(path):
    return parse_environment(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def parse_environment(plan):
    config = plan["environment"]
    cell, = bounds([plan["cell_m"]], 1, "cell_m")
    xmin, xmax, ymin, ymax, floor, _ = next(rectangles([plan["domain"]], 6, "domain"))
    if cell <= 0:
        raise ValueError("cell_m must be positive")
    dimensions = [(xmax - xmin) / cell, (ymax - ymin) / cell]
    width, height = map(round, dimensions)
    if min(width, height) < 1 or any(
        not math.isclose(value, rounded, rel_tol=0, abs_tol=1e-7)
        for value, rounded in zip(dimensions, (width, height))
    ):
        raise ValueError("Domain width and depth must be whole multiples of cell_m")

    if width * height > 20000:
        raise ValueError("Floor plan exceeds 20,000 grid cells")
    default = config.get("default_material", "wood")
    if default not in MATERIALS:
        raise ValueError("Unknown default_material")
    zones = []
    for zone in config.get("materials", []):
        material = zone["material"]
        if material not in MATERIALS:
            raise ValueError(f"Unknown material: {material}")
        box, = rectangles([zone["bounds"]], 4, "material bounds")
        zones.append((box, material))

    footprint = list(rectangles(config["footprint"], 4, "footprint"))
    solids = [b for b in rectangles(plan["solids"], 6, "solid") if b[4] <= floor < b[5]]
    doors = [b for b in rectangles(plan.get("openings", []), 6, "opening") if b[4] <= floor < b[5]]
    walkable = set()
    for row in range(height):
        y = ymin + (row + 0.5) * cell
        for col in range(width):
            x = xmin + (col + 0.5) * cell
            if any(inside(x, y, b) for b in footprint) and (
                not any(inside(x, y, b) for b in solids)
                or any(inside(x, y, b) for b in doors)
            ):
                walkable.add((col, row))

    def index(point, label, allowed):
        x, y = bounds(point, 2, label)
        if inside(x, y, (xmin, xmax, ymin, ymax)):
            grid_point = (math.floor((x - xmin) / cell), math.floor((y - ymin) / cell))
            if grid_point in allowed:
                return grid_point
        raise ValueError(f"{label} {point} must be in reachable, walkable space")

    exits = [index(point, "Exit", walkable) for point in config["exits"]]
    if not exits or len(set(exits)) != len(exits):
        raise ValueError("Provide at least one exit and avoid duplicate exit cells")
    reachable = {exits[0]}
    queue = deque(reachable)
    while queue:
        x, y = queue.popleft()
        for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if point in walkable and point not in reachable:
                reachable.add(point)
                queue.append(point)
    if not set(exits) <= reachable:
        raise ValueError("All exits must connect to the first exit")

    rooms = [
        dict(id=f"room-{i}", name=name, cells=[index(point, name, reachable)])
        for i, (name, point) in enumerate(config["rooms"])
    ]
    materials = {}
    for col, row in reachable:
        x, y = xmin + (col + .5) * cell, ymin + (row + .5) * cell
        for box, material in zones:
            if inside(x, y, box):
                materials[col, row] = material
    scene = Scene(
        default_material=default, materials=materials,
        vents={index(point, "Vent", reachable) for point in config.get("vents", [])},
        id=config["id"], name=config["name"], width=width, height=height,
        resolution=cell, exits=exits, rooms=rooms, source=config["source"],
        walls={(x, y) for y in range(height) for x in range(width)} - reachable,
    )
    scenario = {
        key: [index(point, key, reachable) for point in config[key]]
        for key in ("survivors", "ignitions")
    }
    scenario["seed"] = config.get("seed", 7)
    return scene, scenario
