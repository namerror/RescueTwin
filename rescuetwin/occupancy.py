"""Building-use occupancy priors: where people are likely to be, from static building information only.

A prior is public, pre-incident knowledge (building use from CoStar or the operator, room labels and areas
from the scan). It never reads simulator survivor placements. Weights are illustrative search heuristics,
not occupancy measurements: they change the order in which the robot inspects rooms, nothing else.
"""
from __future__ import annotations

SERVICE_ROOMS = {'Bathroom', 'Closet', 'Utility', 'Laundry', 'Garage', 'Storage'}

# main: multiplier for the main room (largest non-service room); labels: per-label multipliers.
PROFILES = {
    'residential': dict(label='Residential: house or apartment',
                        rationale='Anyone could be anywhere; every room is equally likely.',
                        main=1.0, labels={}),
    'assembly': dict(label='Assembly: yoga/fitness studio, worship, event hall',
                     rationale='Classes and events gather people in the main room.',
                     main=6.0, labels={'Bathroom': .6, 'Kitchen': .5, 'Bedroom': .5}),
    'office': dict(label='Office',
                   rationale='Open-plan floor and meeting rooms hold most people.',
                   main=3.0, labels={'Bathroom': .5, 'Kitchen': .8}),
    'retail': dict(label='Retail store',
                   rationale='Shoppers and staff are mostly on the sales floor.',
                   main=4.0, labels={'Bathroom': .4, 'Kitchen': .4}),
    'restaurant': dict(label='Restaurant or cafe',
                       rationale='Diners in the main dining room; staff in the kitchen.',
                       main=4.0, labels={'Kitchen': 2.0, 'Bathroom': .5}),
}

# CoStar propertyType -> profile. Unknown types fall back to residential (uniform), i.e. no assumption.
COSTAR_TYPES = {
    'Multifamily': 'residential', 'Single-Family': 'residential', 'Residential': 'residential',
    'Student': 'residential', 'Senior Living': 'residential', 'Hospitality': 'residential',
    'Office': 'office', 'Flex': 'office', 'Retail': 'retail',
    'Specialty': 'assembly', 'Sports & Entertainment': 'assembly',
}


def room_area(scene, room):
    return room.get('area_m2') or len(room['cells']) * scene.resolution ** 2


def main_room(scene):
    candidates = [r for r in scene.rooms if (r.get('label') or '') not in SERVICE_ROOMS] or scene.rooms
    return max(candidates, key=lambda r: room_area(scene, r)) if candidates else None


def resolve(scene, building_use=None):
    """Pick the profile and say where the choice came from."""
    if building_use:
        if building_use not in PROFILES: raise ValueError(f'Unknown building use {building_use!r}')
        return building_use, 'operator selection'
    costar = scene.meta.get('costar') or {}
    kind = next((f.get('value') for f in costar.get('fields', []) if f.get('key') == 'property_type'), None)
    if kind in COSTAR_TYPES:
        return COSTAR_TYPES[kind], f'CoStar propertyType "{kind}"'
    return 'residential', 'default (no building type known; uniform prior)'


def weights(scene, building_use=None):
    """Room id -> relative likelihood that people are there."""
    use, source = resolve(scene, building_use)
    profile = PROFILES[use]; main = main_room(scene)
    out = {}
    for room in scene.rooms:
        w = profile['labels'].get(room.get('label') or '', 1.0)
        if main is not None and room['id'] == main['id']: w *= profile['main']
        out[room['id']] = w
    return dict(use=use, source=source, label=profile['label'], rationale=profile['rationale'],
                main_room=(main or {}).get('id'), weights=out)


def describe(scene, prior):
    main = next((r for r in scene.rooms if r['id'] == prior['main_room']), None)
    text = f"Search prior: {prior['label']} ({prior['source']}). {prior['rationale']}"
    if main and PROFILES[prior['use']]['main'] != 1.0:
        text += f" Main room {main.get('label') or main['name']} ({room_area(scene, main):.0f} m²) weighted {PROFILES[prior['use']]['main']:g}×."
    return text
