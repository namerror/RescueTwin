"""Image perception: YOLO person detection on rendered RGB-D frames, independent of simulator entity IDs.

Camera convention (shared with the frontend renderer):
- RGB/depth are W x H, row 0 at the top. Intrinsics fx, fy, cx, cy are in pixels (u right, v down).
- Depth is metres along the camera's forward axis; 0 or non-finite means invalid (sky, beyond range).
- Pose is the camera position in the model frame (x, y, z up) and yaw (radians, 0 = +x, CCW).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import base64
import io
import math
import os

import numpy as np

PERSON_CONFIDENCE = 0.35
MIN_PERSON_HEIGHT_M = 0.2    # projected box top must be above this height
MAX_FRAME_PIXELS = 1280 * 960


@dataclass
class Detection:
    box: list[float]
    confidence: float
    position: list[float] | None   # camera frame [right, down, forward] metres


class PersonDetector:
    def __init__(self, weights=None, device=None):
        from ultralytics import YOLO
        self.weights = weights or os.environ.get('YOLO_WEIGHTS', 'yolo26n.pt')
        self.model = YOLO(self.weights)
        if device is None:
            try:
                import torch
                device = 'mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu'
            except ImportError:
                device = 'cpu'
        self.device = device
        if device == "cpu":
            # Avoid CPU thread oversubscription starving the browser/simulator.
            import torch
            torch.set_num_threads(max(1, int(os.environ.get("RESCUETWIN_VISION_THREADS", "2"))))

    def detect(self, image, depth=None, intrinsics=None):
        """Depth is metres along camera Z; returned position is in camera coordinates."""
        result = self.model.predict(image, classes=[0], conf=PERSON_CONFIDENCE, device=self.device, verbose=False)[0]
        detections = []
        for box in result.boxes:
            bounds = [float(v) for v in box.xyxy[0].tolist()]
            position = None
            if depth is not None and intrinsics is not None:
                position = back_project(bounds, depth, intrinsics)
            detections.append(Detection(bounds, float(box.conf[0]), position))
        return detections


def back_project(bounds, depth, intrinsics):
    """Robust depth from the central part of the box (limbs/background excluded), then pinhole back-projection."""
    depth = np.asarray(depth, dtype=np.float32)
    x1, y1, x2, y2 = bounds
    w, h = x2 - x1, y2 - y1
    c0, c1 = int(max(0, x1 + .3 * w)), int(min(depth.shape[1], x2 - .3 * w + 1))
    r0, r1 = int(max(0, y1 + .2 * h)), int(min(depth.shape[0], y2 - .3 * h + 1))
    patch = depth[r0:r1, c0:c1]
    values = patch[np.isfinite(patch) & (patch > 0)]
    if values.size == 0:
        return None
    z = float(np.percentile(values, 40))
    fx, fy, cx, cy = intrinsics
    u, v = (x1 + x2) / 2, (y1 + y2) / 2
    return [(u - cx) * z / fx, (v - cy) * z / fy, z]


def camera_to_world(position, pose):
    """[right, down, forward] in the camera -> model-frame (x, y, z)."""
    right, down, forward = position
    yaw = pose['yaw']
    fx_, fy_ = math.cos(yaw), math.sin(yaw)
    rx_, ry_ = math.sin(yaw), -math.cos(yaw)
    return (pose['x'] + forward * fx_ + right * rx_, pose['y'] + forward * fy_ + right * ry_, pose['z'] - down)


def decode_rgb(data):
    from PIL import Image
    if ',' in data[:64]:
        data = data.split(',', 1)[1]
    image = Image.open(io.BytesIO(base64.b64decode(data, validate=True))).convert('RGB')
    if image.width * image.height > MAX_FRAME_PIXELS:
        raise ValueError('Frame too large')
    return image


def decode_depth(data, width, height):
    raw = base64.b64decode(data, validate=True)
    if len(raw) != width * height * 4:
        raise ValueError('Depth buffer size does not match frame dimensions')
    return np.frombuffer(raw, dtype='<f4').reshape(height, width)


def process_frame(detector, image, depth, intrinsics, pose, max_range):
    """Run detection and project to the model frame. Returns (accepted, rejected) evidence dicts."""
    accepted, rejected = [], []
    for d in detector.detect(image, depth, intrinsics):
        item = dict(box=[round(v, 1) for v in d.box], confidence=round(d.confidence, 3))
        if d.position is None:
            item['reason'] = 'no valid depth'
            rejected.append(item)
            continue
        world = camera_to_world(d.position, pose)
        item.update(camera=[round(v, 3) for v in d.position], world=[round(v, 3) for v in world])
        top = pose['z'] - (d.box[1] - intrinsics[3]) * d.position[2] / intrinsics[1]
        if d.position[2] > max_range:
            item['reason'] = 'beyond depth range'
            rejected.append(item)
        elif top < MIN_PERSON_HEIGHT_M:
            item['reason'] = 'implausible height'
            rejected.append(item)
        else:
            accepted.append(item)
    return accepted, rejected


__all__ = ['Detection', 'PersonDetector', 'back_project', 'camera_to_world', 'decode_rgb', 'decode_depth',
           'process_frame', 'asdict']
