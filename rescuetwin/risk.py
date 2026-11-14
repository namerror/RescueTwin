"""Room fire-risk map: pre-incident fixture information that changes the order rooms are searched.

Rooms with a gas heater are searched first (an ignition source near occupants), rooms with fire sprinklers
slightly later (suppression buys time), and rooms with neither sit in the middle. The map multiplies the
building-use search prior; it never reads simulator survivor or ignition truth, and it is not sensor data.

Sources, in order of precedence:
- operator tags per room (``scenario['room_risk']``), entered in the scenario editor;
- the CoStar building default, applied to every room. The kit records sprinklers and gas heating per building
  or home, never per room: commercial-property ``hasSprinklers`` (null = unknown, not no) and, for
  residential-listing-features joins, a ``gas_heating`` field ("Heating System Uses Gas"). Lawn "Sprinkler
  System" lines under Lot Details are irrigation and must never be mapped to fire sprinklers.
"""
from __future__ import annotations

FACTORS = {'gas_heater': 1.5, 'both': 1.2, 'neither': 1.0, 'unknown': 1.0, 'sprinkler': 0.8}
LABELS = {'gas_heater': 'Gas heater', 'sprinkler': 'Fire sprinkler', 'both': 'Gas heater + sprinkler',
          'neither': 'Neither', 'unknown': 'Unknown'}
OPERATOR_TAGS = ('gas_heater', 'sprinkler', 'both', 'neither')   # 'unknown' is the absence of a tag
NOTE = 'Pre-incident fixture tags (operator-entered or CoStar building default); search order only, not sensor data.'


def _field(scene, key):
    costar = (scene.meta or {}).get('costar') or {}
    return next((f for f in costar.get('fields', []) if f.get('key') == key), None)


def _combine(gas, sprinkler):
    """gas/sprinkler are True, False or None (unknown). Unknown on either side is never turned into 'no'."""
    if gas and sprinkler: return 'both'
    if gas: return 'gas_heater'
    if sprinkler: return 'sprinkler'
    if gas is False and sprinkler is False: return 'neither'
    return 'unknown'


def building_default(scene):
    """(tag, provenance) for the whole building from the scene's CoStar join."""
    provenance = []
    values = {}
    for name, key in (('sprinkler', 'has_sprinklers'), ('gas', 'gas_heating')):
        entry = _field(scene, key)
        values[name] = None if entry is None else entry.get('value')
        if entry is not None:
            provenance.append({k: entry.get(k) for k in ('key', 'dataset', 'source_id', 'record_type', 'field_path', 'value')})
    tag = _combine(values['gas'], values['sprinkler'])
    if not provenance:
        source = 'no CoStar join'
    else:
        source = 'CoStar ' + ', '.join(f"{p['field_path'] or p['key']}={'null' if p['value'] is None else p['value']}" for p in provenance)
    return tag, dict(source=source, fields=provenance)


def room_map(scene, overrides=None):
    """Room id -> {tag, factor, source, ...}. Overrides must already be validated."""
    overrides = overrides or {}
    default, provenance = building_default(scene)
    out = {}
    for room in scene.rooms:
        operator = overrides.get(room['id'])
        tag = operator or default
        out[room['id']] = dict(id=room['id'], name=room['name'], label=room.get('label'), tag=tag, tag_label=LABELS[tag],
                               factor=FACTORS[tag], source='operator' if operator else 'costar building default',
                               provenance=None if operator else provenance['source'])
    return out


def validate(scene, overrides):
    if overrides is None: return
    if not isinstance(overrides, dict): raise ValueError('room_risk: map room ids to tags')
    ids = {r['id'] for r in scene.rooms}
    for room_id, tag in overrides.items():
        if room_id not in ids: raise ValueError(f'room_risk: unknown room {room_id!r}')
        if tag not in OPERATOR_TAGS: raise ValueError(f'room_risk: tag must be one of {", ".join(OPERATOR_TAGS)}')


def describe(scene, risk):
    counts = {}
    for r in risk.values(): counts[r['tag']] = counts.get(r['tag'], 0) + 1
    _, provenance = building_default(scene)
    parts = ', '.join(f"{n} {LABELS[t].lower()}" for t, n in sorted(counts.items(), key=lambda kv: -FACTORS[kv[0]]))
    return (f"Room risk map: {parts} (default from {provenance['source']}). "
            'Gas heater 1.5x, sprinkler 0.8x search priority; fixture tags, not sensor data.')


def public(scene, risk):
    tag, provenance = building_default(scene)
    return dict(rooms=list(risk.values()), building_default=tag, building_source=provenance['source'],
                building_fields=provenance['fields'],
                tags=[dict(id=t, label=LABELS[t], factor=FACTORS[t]) for t in OPERATOR_TAGS], note=NOTE)
