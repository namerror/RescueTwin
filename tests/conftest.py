import os

# API tests run against the committed synthetic fixture; real bundles are exercised in test_scene.py.
os.environ.setdefault('RESCUETWIN_SCENE', 'development-apartment')
os.environ.setdefault('RESCUETWIN_PRELOAD_DETECTOR', '0')

import pytest


@pytest.fixture(autouse=True)
def teammate_hazard_pacing(monkeypatch, request):
    """Existing hazard tests describe the teammate's model at 1.0x; demo pacing is tested explicitly."""
    if 'demo_pacing' not in request.keywords:
        import rescuetwin.core as core
        monkeypatch.setattr(core, 'HAZARD_TIME_SCALE', 1.0)
