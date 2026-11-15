"""Portable reviewed annotations. Standard library only; no assets or credentials."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA = 'rescuetwin.scene-manifest/1'


def model_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9]+', value):
        raise ValueError('Manifest model IDs must contain only letters and digits')
    return value


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def annotation(value, model):
    """Validate inputs before any download or publication; retain optional annotations."""
    if not isinstance(value, dict) or value.get('model') != model:
        raise ValueError(f'Annotation model must match {model}')
    if not isinstance(value.get('floor_label'), str) or not value['floor_label'].strip():
        raise ValueError(f'{model}: floor_label is required')
    if not finite(value.get('resolution')) or value['resolution'] <= 0:
        raise ValueError(f'{model}: resolution must be positive and finite')
    for key in ('property_id', 'floor_plan_id', 'residential_listing_id'):
        if value.get(key) is not None and (type(value[key]) is not int or value[key] <= 0):
            raise ValueError(f'{model}: {key} must be a positive ID or null')
    exits = value.get('exits')
    if not isinstance(exits, list) or not exits:
        raise ValueError(f'{model}: reviewed exits are required')
    ids = set()
    for exit in exits:
        if not isinstance(exit, dict) or any(not isinstance(exit.get(k), str) or not exit[k].strip()
                                              for k in ('id', 'label', 'verified', 'beyond')):
            raise ValueError(f'{model}: exit ID, label, verification and beyond assumptions are required')
        if exit['id'] in ids:
            raise ValueError(f'{model}: duplicate exit ID')
        ids.add(exit['id'])
        evidence = exit.get('evidence')
        if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
            raise ValueError(f'{model}: exit evidence is required')
        segment = exit.get('segment')
        if not isinstance(segment, list) or len(segment) != 2 or any(
                not isinstance(p, list) or len(p) != 2 or not all(finite(n) for n in p) for p in segment) or segment[0] == segment[1]:
            raise ValueError(f'{model}: exit segment must contain two distinct finite XY points')
    if 'robot_start' in value:
        p = value['robot_start']
        if not isinstance(p, list) or len(p) != 2 or any(type(n) is not int for n in p):
            raise ValueError(f'{model}: robot_start must be a grid cell')
    if 'floor_elevation_m' in value and not finite(value['floor_elevation_m']):
        raise ValueError(f'{model}: floor elevation must be finite')
    if 'source_to_model' in value:
        m = value['source_to_model']
        if not isinstance(m, list) or len(m) != 4 or any(
                not isinstance(row, list) or len(row) != 4 or not all(finite(n) for n in row) for row in m) or m[3] != [0, 0, 0, 1]:
            raise ValueError(f'{model}: source_to_model must be a finite affine 4x4 matrix')
        det = (m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1])
               - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
               + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))
        if abs(det) < 1e-9:
            raise ValueError(f'{model}: source_to_model must be invertible')
    return value


def validate(data):
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('Unsupported scene manifest schema')
    entries = data.get('scenes')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Manifest must contain scenes')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Manifest scene must be an object')
        model = model_id(entry.get('model'))
        if model in seen:
            raise ValueError(f'Duplicate model ID: {model}')
        seen.add(model)
        annotation(entry.get('annotation'), model)
    model_id(data.get('default_model'))
    if data['default_model'] not in seen:
        raise ValueError('Manifest default_model must name an included model')
    return data


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def annotation_path(root, model):
    return root/'scenes'/f'{model}.json'


def local_annotation(root, model):
    manifest = root/'data/prepared'/model/'bundle/scene.json'
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        if data.get('annotation') is not None:
            return data['annotation']
    return json.loads(annotation_path(root, model).read_text())


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name+'.next')
    try:
        pending.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def export(root, destination, default_model):
    """Check source bundle assets without importing application dependencies."""
    entries = []
    for path in sorted((root/'data/prepared').glob('*/bundle/scene.json')):
        data = json.loads(path.read_text())
        if data.get('source') == 'synthetic':
            continue
        model = model_id(path.parent.parent.name)
        try:
            recipe = local_annotation(root, model)
        except FileNotFoundError:
            continue
        annotation(recipe, model)
        mesh = data.get('mesh') or {}
        if (data.get('schema') != 'rescuetwin.scene/1' or data.get('source') != 'sponsor-source'
                or (data.get('frame') or {}).get('model') != model
                or not mesh.get('textured') or not mesh.get('validation', {}).get('passed')
                or not data.get('checks', {}).get('passed') or not data.get('exits')):
            raise ValueError(f'{model}: export requires a validated textured sponsor scene')
        assets = data.get('assets') or {}
        if mesh.get('file') not in assets or not mesh.get('materials') or any(n not in assets for n in mesh['materials']):
            raise ValueError(f'{model}: incomplete mesh/material declarations')
        for name, info in assets.items():
            asset = (path.parent/name).resolve()
            if (Path(name).name != name or not asset.is_relative_to(path.parent.resolve())
                    or not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != info['sha256']):
                raise ValueError(f'{model}: missing or corrupt bundle asset')
        if data.get('annotation_sha256') and data['annotation_sha256'] != digest(recipe):
            raise ValueError(f'{model}: annotation does not match prepared bundle; rebuild before export')
        entries.append({'model': model, 'annotation': recipe})
    models = [entry['model'] for entry in entries]
    if not models:
        raise ValueError('No annotated prepared scenes to export')
    default = default_model if default_model in models else ('9A46aT7FCPg' if '9A46aT7FCPg' in models else next(iter(models), None))
    manifest = validate({'schema': SCHEMA, 'default_model': default, 'scenes': entries})
    write_json(destination, manifest)
    return manifest
