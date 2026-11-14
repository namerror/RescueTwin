import os

# API tests run against the committed synthetic fixture; real bundles are exercised in test_scene.py.
os.environ.setdefault('RESCUETWIN_SCENE', 'development-apartment')
os.environ.setdefault('RESCUETWIN_PRELOAD_DETECTOR', '0')
