"""Operator-authored synthetic appearances; never demographics inferred by perception."""
VARIANTS = ('adult-male', 'adult-female', 'child-male', 'child-female', 'older-male', 'older-female')


def validate(value):
    if value is not None and value not in (*VARIANTS, 'mixed'):
        raise ValueError('Unknown human_variant')
