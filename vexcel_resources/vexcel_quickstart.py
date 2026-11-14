"""
Vexcel API 2.0 - Hackathon Quickstart
-------------------------------------
From a single latitude/longitude:
  1. Get Elements property attributes (parcel + structures)
  2. Get ortho + oblique imagery clipped to the parcel polygon
  3. Detect custom elements (any plain-language label) on the property:
     bounding boxes AND object masks, drawn on the ortho and an oblique
  4. Run an embeddings (semantic) search over a large area around it

Setup:
    pip install requests shapely pillow

    The script reads your key from an environment variable named VEXCEL_API_KEY.
    IMPORTANT: an environment variable only exists in the terminal where you set
    it. Set it and run the script in the SAME terminal window:

        macOS / Linux:       export VEXCEL_API_KEY="your-long-life-key"
        Windows PowerShell:  $env:VEXCEL_API_KEY = "your-long-life-key"
        Windows cmd:         set VEXCEL_API_KEY=your-long-life-key
        then:                python vexcel_quickstart.py

    A different terminal tab or window will NOT see it. That includes VS Code's
    Run / Debug buttons, which can start their own terminal. If you set the
    variable in one terminal and the script says it is missing, run the script
    from that same terminal instead.

    To set it permanently (every new terminal sees it):
        Windows:             setx VEXCEL_API_KEY "your-long-life-key"
                             (then close and reopen the terminal, or restart VS Code)
        macOS / Linux:       add the export line to ~/.zshrc or ~/.bashrc,
                             then open a new terminal
"""
import base64
import io
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import requests
from PIL import Image                      # decodes the mask images
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union
from shapely import wkt as shapely_wkt

API = "https://api.vexcelgroup.com/v2"
KEY = os.environ.get("VEXCEL_API_KEY")
if not KEY:
    sys.exit("VEXCEL_API_KEY is not set in this terminal.\n"
             "Set it in the SAME terminal you run this script from (see the Setup notes\n"
             "at the top of this file), or set it permanently, then run the script again.")
# Long-life API keys go in the X-API-KEY header (recommended).
# Alternative: add  api_key=<your-key>  as a query parameter / body field instead.
HEADERS = {"X-API-KEY": KEY}

# ====================== CHANGE THESE (one place) ======================
# Any location inside the area your API key covers, in decimal degrees.
# (The default is just an example location in Houston, TX.)
LAT, LON = 29.750043, -95.406594  
# Step 4 embeddings search area: a SQUARE (not a circle) centered on LAT, LON.
# This is the distance from the center to each edge, i.e. half the side
# (3000 -> a 6 km x 6 km square). Smaller is faster and more exact.
SEARCH_RADIUS_M = 3000
# Only used if there is no property record (parcel) at your point: the script then
# looks at a square around the point instead. Distance from the center to each
# edge in meters (40 -> an 80 m x 80 m square).
NO_PARCEL_HALF_M = 40
# =====================================================================

# NOTE: WKT is (longitude latitude) - the reverse of LAT, LON above!
POINT = f"POINT({LON} {LAT})"
# Imagery "layers" (what kind of imagery covers a place):
#   urban      7.5 cm ortho AND oblique (most cities)
#   urban-r    7.5 cm ortho, no obliques of its own
#   wide-area  15 cm ortho, no obliques (rural areas and smaller towns)
#   urban-g    7.5 cm ortho, other coverage
# Asking for all of them lets the API pick the best available at each location,
# so this script works anywhere your key covers. We pass LAYERS to every ortho
# request below. Only oblique requests use "urban", because that is the only
# layer with obliques (some urban-r places also have urban obliques, so we check
# what exists instead of assuming).
LAYERS = "urban,urban-r,wide-area,urban-g"   # bluesky layers

# All files this script creates (JSON and images) go into an "output" folder
# next to this script, wherever you run it from.
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------- helpers
MAX_RETRIES = 5


def send(method, path, **kwargs):
    """Call the API. If it answers 429 Too Many Requests, wait and retry with
    exponential backoff (about 1, 2, 4, 8, 16 s plus a little random jitter)."""
    for attempt in range(MAX_RETRIES + 1):
        r = requests.request(method, f"{API}/{path}", headers=HEADERS, **kwargs)
        if r.status_code != 429 or attempt == MAX_RETRIES:
            return r
        wait = min(2 ** attempt, 30) + random.random()
        print(f"  429 rate limited - retrying in {wait:.1f}s "
              f"(retry {attempt + 1}/{MAX_RETRIES})")
        time.sleep(wait)


def get(path, **params):
    """GET a JSON endpoint."""
    r = send("GET", path, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def post(path, body, expect_json=True):
    """POST a JSON body; return JSON or raw bytes (for imagery)."""
    r = send("POST", path, json=body, timeout=300)
    if not r.ok:
        raise RuntimeError(f"/{path} failed ({r.status_code}): {r.text[:500]}")
    return r.json() if expect_json else r.content


def to_wkt(geometry):
    """Geometry may arrive as GeoJSON (dict) or as a WKT string - normalize to WKT."""
    if isinstance(geometry, str):
        return shapely_wkt.loads(geometry).wkt
    return shape(geometry).wkt


def square_around(lat, lon, half_size_m):
    """A WKT polygon square centered on lat/lon, half_size_m meters from center to edge."""
    dlat = half_size_m / 111_320
    dlon = half_size_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def save(name, data):
    """Write bytes to OUTPUT_DIR/name."""
    with open(OUTPUT_DIR / name, "wb") as f:
        f.write(data)
    print(f"  saved {name} ({len(data)//1024} KB)")


print(f"Output files will be saved in: {OUTPUT_DIR}")

# ------------------------------------------------ 1. Elements property data
print("1. Elements property attributes")
r = send("GET", "property/extract", timeout=120, params={
    "wkt": POINT, "srid": 4326,
    "layer": LAYERS,
    "latest": "true",            # most recent collection only
    "include": "precomputed",    # everything stored for the parcel + structures
    "unit-system": "imperial"})
if r.status_code == 401:
    sys.exit("401 Unauthorized: the API rejected your key. Check VEXCEL_API_KEY "
             "(see the Setup notes at the top of this file).")

if r.status_code == 404:
    # HTTP 404 "No available property data for request" means there is no property
    # record (parcel) at this exact point. Typical causes: the point is on a road,
    # in water or open land, or outside the area your key covers, or lat and lon
    # are swapped. A parcel is NOT required for the rest of this script: the
    # imagery and detection endpoints accept any WKT area. So we fall back to a
    # small square around the point and carry on (without property attributes).
    print("  No property data at this point (HTTP 404). Typical causes: the point is on a")
    print("  road, in water or open land, or outside your key's area, or lat/lon are swapped.")
    print(f"  Carrying on with a square around your point ({2 * NO_PARCEL_HALF_M} m across) instead of a parcel.")
    prop, attrs = None, {}
    parcel_wkt = square_around(LAT, LON, NO_PARCEL_HALF_M)
    area_name = f"{2 * NO_PARCEL_HALF_M} m square around your point (NO PARCEL)"
else:
    r.raise_for_status()
    prop = r.json()
    parcel = prop["features"][0]
    attrs = parcel["properties"]
    parcel_wkt = to_wkt(parcel["geometry"])
    area_name = "parcel"

    print("  collection      :", attrs.get("metadata.collection"))
    print("  structures      :", attrs.get("structure.count"))
    print("  parcel tree %   :", attrs.get("parcel-tree-cover.percent"))
    print("  pool detected   :", attrs.get("pool.detected"))
    for s in attrs.get("structures", []):
        if s.get("primary-structure"):
            print("  primary roof    :", s.get("roof-characteristics.material.class"),
                  s.get("roof-characteristics.shape.value"),
                  "| condition", s.get("roof-characteristics.condition"))
            print("  footprint sq ft :", round(s.get("footprint.area", 0)))

    with open(OUTPUT_DIR / "property.json", "w") as f:
        json.dump(prop, f, indent=2)
    print("  saved property.json (the full response, open it to browse every attribute)")


# ------------------------------------- 2. Ortho + oblique imagery of parcel
# From here on, parcel_wkt is either the parcel polygon from Step 1, or (when the
# point had no property record) the small square around your point. The imagery
# and detection calls work the same either way: they only need a WKT area. In the
# pictures, the gold outline is that area (the parcel, or the square).
print(f"2. Imagery clipped to the {area_name}")
outline = {"wkt": parcel_wkt, "outline-width": 4}

ortho = post("ortho/extract", {
    "wkt": parcel_wkt, "srid": 4326, "layer": LAYERS,   # best available layer here
    "buffer-meter": 10,                 # a little context around the lot
    "crop": "bounding_box",
    "image-format": "jpeg",
    "drawing": [{**outline, "outline-color": "rgb(255,204,0)"}],
}, expect_json=False)
save("ortho_parcel.jpg", ortho)

# Oblique imagery only exists in the "urban" layer. Places covered only by
# urban-r or wide-area (rural areas, smaller towns) have NO obliques, and even
# where obliques exist not every direction may be there. So ask the coverage
# service which ones exist here (newest collection) instead of trying each and
# failing. When there are none, the service answers 404, "directions" ends up
# empty, and the oblique steps below (and in Step 3) are simply skipped.
r = send("GET", "oriented/collections", timeout=120, params={
    "wkt": parcel_wkt, "srid": 4326, "layer": "urban", "product-type": "oblique",
    "include": "product-type,collection,last-capture-date"})
if r.status_code != 404:            # 404 = no oblique coverage at this location
    r.raise_for_status()
features = r.json()["features"] if r.ok else []
newest = max((f["properties"]["last-capture-date"] for f in features), default=None)
available = {f["properties"]["product-type"] for f in features
             if f["properties"]["last-capture-date"] == newest}
directions = [d for d in ["oblique-north", "oblique-south", "oblique-east", "oblique-west"]
              if d in available]
if directions:
    print("  oblique directions available:", ", ".join(directions))
else:
    print("  no oblique imagery here (only the 'urban' layer has obliques): skipping obliques")

for direction in directions:
    img = post("oriented/extract", {
        "wkt": parcel_wkt, "srid": 4326, "layer": "urban",
        "product-type": direction,
        "rotation": "natural",
        "buffer-meter": 10,
        "crop": "bbox",
        "image-format": "jpeg/png",
        # oriented/extract quirks: bare "r,g,b" colors and an explicit
        # transparent fill are needed for the outline to render
        "drawing": [{**outline, "outline-color": "255,204,0",
                     "fill-color": "255,204,0", "transparency": 0}],
    }, expect_json=False)
    save(f"{direction}.jpg", img)


# ------------------------------------------- 3. Custom element detection
print(f"3. Custom elements (open-vocabulary detection) inside the {area_name}")
labels = ["swimming pool", "car"]

# The imagery to run the detection on. Each entry is an ordinary extract request.
# Here: the ortho (any layer), plus the north-facing oblique when it exists at
# this location (it will not in urban-r or wide-area places: then the detection
# just runs on the ortho). The other oblique directions work the same way: add
# one more entry with "product-type": "oblique-south", "oblique-east" or
# "oblique-west".
images = [{
    "endpoint": "ortho/extract",
    # Must be the SAME source image as the overlay below, otherwise the
    # boxes land on a different capture/ortho and appear shifted.
    "request": {"layer": LAYERS, "srid": 4326, "wkt": parcel_wkt,
                "crop": "bounding_box"},
}]
if "oblique-north" in directions:
    images.append({
        "endpoint": "oriented/extract",
        "request": {"layer": "urban", "srid": 4326, "wkt": parcel_wkt,
                    "product-type": "oblique-north", "rotation": "natural",
                    "crop": "bbox"},
    })

detect = post("elements/detect", {
    "wkt": parcel_wkt, "srid": 4326,
    "buffer-meter": 0,                  # 0 = only the parcel itself (no neighbors)
    "images": images,
    "model-input": {
        "elements": [{"label": l, "model": "sam3", "detection-threshold": 0.5}
                     for l in labels],
        # bbox = box as a WKT polygon. pixel-mask = the object's outline as a small
        # picture (see mask_to_wkt below). The API requires pixel-bbox whenever
        # pixel-mask is requested.
        "include": ["bbox", "confidence", "pixel-mask", "pixel-bbox"],
    },
})
PALETTE = ["rgb(0,229,255)",   # cyan
           "rgb(255,64,255)",  # magenta
           "rgb(80,255,80)",   # green
           "rgb(255,120,0)",   # orange
           "rgb(255,255,255)", # white
           "rgb(255,64,64)"]   # red
label_colors = {l: PALETTE[i % len(PALETTE)] for i, l in enumerate(labels)}


def bare(color):
    """oriented/extract wants colors as "r,g,b" instead of "rgb(r,g,b)"."""
    return color.replace("rgb(", "").replace(")", "")


# A detection's pixel-mask is a base64 PNG, white where the object is, cropped to
# the detection's box. To draw it, turn it into a polygon in map coordinates:
# trace the white pixels, then stretch that polygon onto the 4 corners of the
# detection's map bbox. Which bbox corner is the picture's top-left etc. depends
# on the viewing direction, so for each view we say where "up" and "right" point
# in the picture, as (east, north) vectors.
VIEW_AXES = {"ortho": ((0, 1), (1, 0)),            # up = north, right = east
             "oblique-north": ((0, 1), (1, 0)),    # looking north
             "oblique-south": ((0, -1), (-1, 0)),  # looking south: up = south, right = west
             "oblique-east": ((1, 0), (0, -1)),    # looking east: up = east, right = south
             "oblique-west": ((-1, 0), (0, 1))}    # looking west: up = west, right = north


def mask_to_wkt(mask_png_b64, bbox_wkt, view):
    """Mask picture -> polygon (WKT, map coordinates) lying on the object."""
    mask = Image.open(io.BytesIO(base64.b64decode(mask_png_b64))).convert("L")
    w, h = mask.size
    shrink = min(1.0, 64 / max(w, h))              # 64 px is plenty for an outline
    mask = mask.resize((max(1, round(w * shrink)), max(1, round(h * shrink))))
    w, h = mask.size
    px = mask.load()
    runs = []                                      # one thin rectangle per row of white pixels
    for y in range(h):
        x = 0
        while x < w:
            if px[x, y] > 128:
                start = x
                while x < w and px[x, y] > 128:
                    x += 1
                runs.append(box(start, y, x, y + 1))
            else:
                x += 1
    if not runs:
        return None
    shape_px = unary_union(runs).simplify(0.6)     # merge them into one outline
    if shape_px.geom_type != "Polygon":            # several blobs: keep the biggest
        shape_px = max(shape_px.geoms, key=lambda g: g.area)
    # the 4 corners of the map bbox, matched to the picture's corners
    quad = list(shapely_wkt.loads(bbox_wkt).exterior.coords)[:4]
    cx = sum(p[0] for p in quad) / 4
    cy = sum(p[1] for p in quad) / 4
    up, right = VIEW_AXES[view]

    def corner(sign_up, sign_right):
        return max(quad, key=lambda p: sign_up * ((p[0] - cx) * up[0] + (p[1] - cy) * up[1])
                   + sign_right * ((p[0] - cx) * right[0] + (p[1] - cy) * right[1]))
    tl, tr, br, bl = corner(1, -1), corner(1, 1), corner(-1, 1), corner(-1, -1)

    def to_map(x, y):                              # picture pixel -> map coordinates
        u, v = x / w, y / h
        return tuple((1 - v) * ((1 - u) * tl[i] + u * tr[i]) + v * ((1 - u) * bl[i] + u * br[i])
                     for i in (0, 1))
    return Polygon([to_map(x, y) for x, y in shape_px.exterior.coords]).wkt


# Each detection is tagged with the image it was found in ("imageId"), and its
# bbox is a WKT polygon in map coordinates. So it can be drawn straight back
# onto that same image: the ortho, or the oblique photo. We draw the object's
# mask (a see-through tinted shape) first and its bbox outline on top. The mask
# gets no label: the bbox label already says what it is.
for sent, got in zip(images, detect["images"]):
    oriented = sent["endpoint"] == "oriented/extract"
    view = sent["request"]["product-type"] if oriented else "ortho"
    tint = bare if oriented else (lambda c: c)
    boxes = []
    for feat in detect["features"]:
        label = feat["properties"]["label"]
        color = tint(label_colors.get(label, PALETTE[0]))
        for d in feat["properties"]["detections"]:
            if d["imageId"] != got["image-id"]:
                continue
            print(f"  {got['image-id']:<11} {label:<14} conf={d['confidence']:.2f}")
            mask_wkt = mask_to_wkt(d["pixel-mask"], d["bbox"], view) if d.get("pixel-mask") else None
            if mask_wkt:                # mask: thin outline + 50% see-through fill, no label
                boxes.append({"wkt": mask_wkt, "outline-color": color, "outline-width": 1,
                              "fill-color": color, "transparency": 50})
            bbox = {"wkt": d["bbox"], "outline-color": color, "outline-width": 4,
                    "label": f"{label} {d['confidence']:.2f}",
                    "label-color": color, "label-size": 22}
            if oriented:                # oriented quirk: explicit transparent fill
                bbox.update({"fill-color": color, "transparency": 0})
            boxes.append(bbox)
    parcel_line = {**outline, "outline-width": 3, "outline-color": tint("rgb(255,204,0)")}
    if oriented:
        parcel_line.update({"fill-color": tint("rgb(255,204,0)"), "transparency": 0})
    overlay = post(sent["endpoint"], {
        **sent["request"], "buffer-meter": 0,
        "image-format": "jpeg/png" if oriented else "jpeg",
        "drawing": [parcel_line] + boxes,
    }, expect_json=False)
    save(f"detections-{sent['request']['product-type']}.jpg" if oriented
         else "detections-ortho.jpg", overlay)


# ---------------------------------------------- 4. Embeddings search
# WHERE does this search? In a SQUARE centered on LAT, LON (see SEARCH_RADIUS_M
# at the top): 3000 m means a 6 km x 6 km square, so its corners are about
# 4.2 km from the center. Every pre-computed embedding tile inside the square
# (zoom-19 tiles, about 60 m wide, in urban areas) is compared with the query,
# and the best matches come back ranked by score. No imagery is downloaded
# for the search itself; we only fetch pictures of the top 3 afterwards.
query = "tennis court"                 # or search-location / search-url / search-image
side_km = 2 * SEARCH_RADIUS_M / 1000
area = square_around(LAT, LON, SEARCH_RADIUS_M)
west, south, east, north = shapely_wkt.loads(area).bounds
print(f"4. Embeddings: semantic search over a {side_km:g} km x {side_km:g} km square")
print(f"  centered on : lat {LAT}, lon {LON} (your location)")
print(f"  search area : lat {south:.4f} to {north:.4f}, lon {west:.4f} to {east:.4f}")
print(f"  looking for : \"{query}\"")


def where(lat, lon):
    """Distance and compass direction of a point from the search center."""
    north_m = (lat - LAT) * 111_320
    east_m = (lon - LON) * 111_320 * math.cos(math.radians(LAT))
    compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
        round(math.degrees(math.atan2(east_m, north_m)) / 45) % 8]
    return f"{math.hypot(east_m, north_m) / 1000:.1f} km {compass} of center"


emb = post("embedding/extract", {
    "wkt": area, "srid": 4326,
    "search-text": query,
    "include": "score,geometry,collection",
    "total-records": 10, "page-size": 10,
})
print(f"  {len(emb['features'])} best-matching tiles returned; saving the top 3:")
for i, feat in enumerate(emb["features"][:3], 1):
    tile_wkt = to_wkt(feat["geometry"])
    center = shapely_wkt.loads(tile_wkt).centroid
    print(f"  #{i} score={feat['properties']['score']:.3f}  "
          f"lat {center.y:.4f}, lon {center.x:.4f}  ({where(center.y, center.x)})")
    tile = post("ortho/extract", {
        "wkt": tile_wkt, "srid": 4326, "layer": LAYERS, "crop": "bounding_box",
        "destination-dimensions": "400,400", "image-format": "jpeg",
    }, expect_json=False)
    save(f"match_{i}.jpg", tile)

print(f"Done. Output files are in: {OUTPUT_DIR}")
