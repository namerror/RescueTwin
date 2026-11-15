"""Room fire-risk map: pre-incident fixture information that changes the order rooms are searched.

Rooms with a gas source are searched first (an ignition source near occupants), rooms with fire sprinklers
slightly later (suppression buys time), and rooms with neither sit in the middle. The map multiplies the
building-use search prior; it never reads simulator survivor or ignition truth, and it is not sensor data.

Sources, in order of precedence:
- operator tags per room (``scenario['room_risk']``), entered in the scenario editor;
- the CoStar building default, applied to every room. The kit records sprinklers and gas appliances per building
  or home, never per room: commercial-property ``hasSprinklers`` (null = unknown, not no) and, for
  residential-listing-features joins, gas heating and appliance fields. Lawn "Sprinkler
  System" lines under Lot Details are irrigation and must never be mapped to fire sprinklers.
"""
from __future__ import annotations

FACTORS = {'gas_heater': 1.5, 'both': 1.2, 'neither': 1.0, 'unknown': 1.0, 'sprinkler': 0.8}
LABELS = {'gas_heater': 'Gas source', 'sprinkler': 'Fire sprinkler', 'both': 'Gas source + sprinkler',
          'neither': 'Neither', 'unknown': 'Unknown'}
OPERATOR_TAGS = ('gas_heater', 'sprinkler', 'both', 'neither')   # 'unknown' is the absence of a tag
NOTE = 'Room search priority uses simulated sprinklers, operator tags, and CoStar building defaults.'


def _field(scene, key):
    costar = (scene.meta or {}).get('costar') or {}
    return next((f for f in costar.get('fields', []) if f.get('key') == key), None)


def recorded_sprinklers(scene):
    """Return CoStar's explicit fire-sprinkler status, or None if unknown."""
    present = (_field(scene, 'has_sprinklers') or {}).get('value')
    if type(present) is bool:
        return present
    system = (_field(scene, 'sprinkler_type') or {}).get('value')
    if system in ('Wet', 'Dry', 'Esfr'):
        return True
    return None


def _combine(gas, sprinkler):
    """gas/sprinkler are True, False or None (unknown). Unknown on either side is never turned into 'no'."""
    if gas and sprinkler: return 'both'
    if gas: return 'gas_heater'
    if sprinkler: return 'sprinkler'
    if gas is False and sprinkler is False: return 'neither'
    return 'unknown'


def building_default(scene):
    """(tag, provenance) for the whole building from the scene's CoStar join."""
    fields = ((scene.meta or {}).get('costar') or {}).get('fields', [])
    gas_keys = {'gas_heating', 'gas_water_heater', 'gas_range', 'gas_mixed_range',
                'gas_oven', 'gas_cooktop', 'gas_fireplace', 'gas_cooling'}
    gas_values = [field.get('value') for field in fields if field.get('key') in gas_keys]
    gas = True if any(value is True for value in gas_values) else None
    if gas_values and all(value is False for value in gas_values):
        gas = False
    provenance = [
        {key: field.get(key) for key in ('key', 'dataset', 'source_id', 'record_type', 'field_path', 'value')}
        for field in fields if field.get('key') in gas_keys | {'has_sprinklers', 'sprinkler_type'}
    ]
    tag = _combine(gas, recorded_sprinklers(scene))
    if not provenance:
        source = 'no CoStar join'
    else:
        source = 'CoStar ' + ', '.join(f"{p['field_path'] or p['key']}={'null' if p['value'] is None else p['value']}" for p in provenance)
    return tag, dict(source=source, fields=provenance)


def room_map(scene, overrides=None, sprinkler_rooms=()):
    """Room id -> {tag, factor, source, ...}. Overrides must already be validated."""
    overrides = overrides or {}
    default, provenance = building_default(scene)
    out = {}
    for room in scene.rooms:
        operator = overrides.get(room['id'])
        installed = room['id'] in sprinkler_rooms
        fallback = _combine(default in ('gas_heater', 'both'), True) if installed else default
        tag = operator or fallback
        if installed and operator:
            tag = _combine(operator in ('gas_heater', 'both'), True)
        source = 'operator' if operator else 'costar building default'
        if installed:
            source = 'simulated sprinkler + ' + source
        out[room['id']] = dict(id=room['id'], name=room['name'], label=room.get('label'), tag=tag, tag_label=LABELS[tag],
                               factor=FACTORS[tag], source=source, default_tag=fallback, simulated_sprinkler=installed,
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
            'Gas source 1.5x, sprinkler 0.8x search priority; fixture tags, not sensor data.')


def public(scene, risk):
    tag, provenance = building_default(scene)
    return dict(rooms=list(risk.values()), building_default=tag, building_source=provenance['source'],
                building_fields=provenance['fields'],
                tags=[dict(id=t, label=LABELS[t], factor=FACTORS[t]) for t in OPERATOR_TAGS], note=NOTE)
