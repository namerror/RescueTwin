"""Local policy-only HTTP service and ROS motion gateway."""
from argparse import ArgumentParser
import base64
from collections import OrderedDict
import hmac
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Annotated, Literal, Union
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import numpy as np
from pydantic import BaseModel, Field, FiniteFloat
import uvicorn

from .core import Scene
from .gazebo_hazards import ParticleLayout, ParticlePublisher
from .hazards import HazardField
from .policy_motion import Clearance, Motion, SPEED, TURN

HAZARD_STEP_NS = 200_000_000


class LeaseRequest(BaseModel):
    lease_id: str | None = None


class Command(BaseModel):
    command_id: str = Field(min_length=1, max_length=80)
    lease_id: str
    observation_seq: int = Field(ge=0)
    model_config = {'extra': 'forbid'}


class Goal(Command):
    type: Literal['navigate_to_pose']
    x: FiniteFloat
    y: FiniteFloat
    yaw: FiniteFloat


class Velocity(Command):
    type: Literal['velocity']
    linear_mps: FiniteFloat = Field(ge=-SPEED, le=SPEED)
    angular_radps: FiniteFloat = Field(ge=-TURN, le=TURN)


class Stop(Command):
    type: Literal['stop']


CommandRequest = Annotated[Union[Goal, Velocity, Stop], Field(discriminator='type')]


def stamp(header):
    return header.stamp.sec * 1_000_000_000 + header.stamp.nanosec


def yaw(rotation):
    x, y, z, w = rotation.x, rotation.y, rotation.z, rotation.w
    return math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))


def pack_frame(rgb, depth, calibration, source='gazebo_geometric_rgbd'):
    if (rgb.width, rgb.height) != (depth.width, depth.height):
        return None
    if (calibration.width, calibration.height) != (rgb.width, rgb.height):
        return None
    if rgb.encoding not in ('rgb8', 'bgr8') or depth.encoding != '32FC1':
        return None
    color = np.ndarray((rgb.height, rgb.width, 3), dtype='u1', buffer=bytes(rgb.data),
                       strides=(rgb.step, 3, 1))
    if rgb.encoding == 'bgr8':
        color = color[..., ::-1]
    metres = np.ndarray((depth.height, depth.width),
                        dtype='>f4' if depth.is_bigendian else '<f4', buffer=bytes(depth.data),
                        strides=(depth.step, 4)).astype('<f4', copy=True)
    metres[~np.isfinite(metres) | (metres <= 0)] = 0.
    return dict(width=rgb.width, height=rgb.height, intrinsics=list(calibration.k),
                frame='camera_optical', rgb_encoding='rgb8_base64',
                depth_encoding='float32_le_m_base64', invalid_depth_m=0.,
                mount=dict(parent='base_link', xyz_m=[.075, 0., .19], rpy_rad=[0., 0., 0.]),
                rgb=base64.b64encode(color.tobytes()).decode(),
                depth=base64.b64encode(metres.tobytes()).decode(),
                source=source)


class Runtime:
    def __init__(self, config):
        grid = config['map']
        self.map = grid
        scene = Scene(id='policy-floor', name='Policy floor', width=grid['width'],
                      height=grid['height'], origin=tuple(grid['origin']),
                      resolution=grid['resolution'], walls={tuple(c) for c in grid['blocked']},
                      exits=[tuple(config['exit_cell'])])
        raw_ignitions = config['ignitions']
        if not isinstance(raw_ignitions, list) or len(raw_ignitions) > 20:
            raise ValueError('Expected a list of at most 20 ignition cells')
        if any(not isinstance(cell, (list, tuple)) or len(cell) != 2
               or any(type(value) is not int for value in cell) for cell in raw_ignitions):
            raise ValueError('Ignitions must be [column, row] integer pairs')
        self.hazard_ignitions = tuple(map(tuple, raw_ignitions))
        self.hazard_seed = config['seed']
        self.hazard_visuals = config.get('hazard_visuals')
        reserved = set(scene.exits)
        if 'spawn_cell' in config:
            reserved.add(tuple(config['spawn_cell']))
        if len(set(self.hazard_ignitions)) != len(self.hazard_ignitions):
            raise ValueError('Duplicate ignition cells')
        for cell in self.hazard_ignitions:
            if not scene.free(cell):
                raise ValueError(f'Ignition {cell} must be on a free map cell')
            if cell in reserved:
                raise ValueError(f'Ignition {cell} must keep the robot spawn and egress clear')
        if type(self.hazard_seed) is not int:
            raise ValueError('Hazard seed must be an integer')
        if self.hazard_visuals and (self.hazard_visuals['version'] != 1
                or not 1.5 <= self.hazard_visuals['ceiling_m'] <= 6):
            raise ValueError('Invalid hazard visuals configuration')
        self.clearance = Clearance(scene)
        self.motion = Motion(self.clearance)
        self.lock = threading.Lock()
        self.seq = 0
        self.clock_ns = None
        self.clock_at = self.pose_at = self.twist_at = 0.
        self.pose = self.twist = None
        self.pose_ns = self.twist_ns = None
        self.reset_hazards()
        self.frames_pending = OrderedDict()
        self.frame = None
        self.frame_receipt = 0.
        self.contact = None
        self.run_id, self.token = uuid.uuid4().hex, uuid.uuid4().hex + uuid.uuid4().hex

    def latest_pose(self):
        if (self.clock_ns is None or self.pose is None or self.twist is None
                or self.pose_ns is None or self.twist_ns is None
                or abs(self.clock_ns - self.pose_ns) > 500_000_000
                or abs(self.clock_ns - self.twist_ns) > 500_000_000
                or time.monotonic() - min(self.clock_at, self.pose_at, self.twist_at) > .5):
            return None
        return self.pose

    def frame_status(self):
        if self.frame is None:
            return 'missing'
        if (self.clock_ns is None or abs(self.clock_ns - self.frame['stamp_ns']) > 700_000_000
                or time.monotonic() - self.frame_receipt > 1.):
            return 'stale'
        return 'ready'

    def camera(self, key, message):
        capture = stamp(message.header)
        with self.lock:
            record = self.frames_pending.setdefault(capture, {})
            record[key] = message
            while len(self.frames_pending) > 12:
                self.frames_pending.popitem(last=False)
            if len(record) != 3:
                return
            self.frames_pending.pop(capture)
        try:
            source = 'gazebo_particle_rgbd' if self.hazard_visuals else 'gazebo_geometric_rgbd'
            frame = pack_frame(record['rgb'], record['depth'], record['info'], source)
        except (ValueError, BufferError, TypeError):
            return
        if frame is not None:
            with self.lock:
                if self.frame is None or capture > self.frame['stamp_ns']:
                    self.frame = dict(frame, stamp_ns=capture, frame_id=str(capture))
                    self.frame_receipt = time.monotonic()

    def reset_hazards(self):
        scene = self.clearance.scene
        self.hazards = HazardField(scene, self.hazard_ignitions, self.hazard_seed)
        self.particle_layout = (ParticleLayout(scene, self.hazard_visuals['ceiling_m'])
                                if self.hazard_visuals else None)
        self.hazard_tick = None
        self.samples = None

    def observe_clock(self, current):
        if self.clock_ns is not None and current < self.clock_ns:
            self.motion.finish('blocked', 'simulation_clock_reset')
            self.motion.lease_until = 0.
            self.reset_hazards()
            self.pose = self.twist = None
            self.pose_ns = self.twist_ns = None
            self.frame = None
            self.frames_pending.clear()
        if current != self.clock_ns:
            self.clock_ns, self.clock_at = current, time.monotonic()

    def advance_hazards(self):
        if self.clock_ns is None:
            return False
        if self.hazard_tick is None:
            self.hazard_tick = self.clock_ns
            return True
        # Consume a bounded number of missed steps per tick and use exact simulation intervals.
        steps = min((self.clock_ns - self.hazard_tick) // HAZARD_STEP_NS, 10)
        if self.hazard_ignitions:
            for _ in range(steps):
                self.hazards.advance(HAZARD_STEP_NS / 1e9)
        else:
            self.hazards.time += steps * HAZARD_STEP_NS / 1e9
        self.hazard_tick += steps * HAZARD_STEP_NS
        return steps > 0

    def update_samples(self):
        if self.latest_pose() is None:
            self.samples = None
            return
        scene = self.clearance.scene
        x, y = scene.cell(*self.pose[:2])
        if not scene.free((x, y)):
            self.samples = None
            return
        self.samples = dict(stamp_ns=self.hazard_tick, unit='normalized_simulation',
                            heat=float(self.hazards.heat[y, x]), smoke=float(self.hazards.smoke[y, x]))

    def app(self):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        prefix = '/policy/v1/runs/{run_id}'

        @app.exception_handler(RequestValidationError)
        async def invalid_request(_request, error):
            return JSONResponse(status_code=422, content={'detail': [
                {'loc': item['loc'], 'msg': item['msg'], 'type': item['type']}
                for item in error.errors()
            ]})

        def authenticate(run_id, authorization):
            if not hmac.compare_digest((authorization or '').encode(), ('Bearer ' + self.token).encode()):
                raise HTTPException(401, 'invalid run token')
            if run_id != self.run_id:
                raise HTTPException(404, 'run not found')

        @app.get(prefix + '/map')
        def static_map(run_id: str, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            return self.map

        @app.get(prefix + '/observations')
        def observations(run_id: str, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            with self.lock:
                pose = self.latest_pose()
                status = self.frame_status()
                return dict(schema='rescuetwin.policy.observation/1', run_id=self.run_id,
                            observation_seq=self.seq, stamp_ns=self.clock_ns, frame='map',
                            pose=dict(zip(('x', 'y', 'yaw'), pose)) if pose else None,
                            twist=dict(zip(('linear_mps', 'angular_radps'), self.twist)) if pose else None,
                            localization_status='ready' if pose else 'unavailable',
                            hazard=self.samples if pose else None,
                            rgbd=dict(status=status, frame_id=self.frame['frame_id'] if self.frame else None,
                                      stamp_ns=self.frame['stamp_ns'] if self.frame else None))

        @app.get(prefix + '/frames/latest')
        def latest_frame(run_id: str, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            with self.lock:
                status = self.frame_status()
                return dict(status=status, frame=self.frame if status == 'ready' else None)

        @app.post(prefix + '/lease')
        def lease(run_id: str, request: LeaseRequest, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            with self.lock:
                lease_id = self.motion.renew(request.lease_id)
                if lease_id is None:
                    raise HTTPException(409, 'lease unavailable')
                return dict(lease_id=lease_id, expires_in_s=1.)

        @app.post(prefix + '/commands')
        def submit(run_id: str, request: CommandRequest, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            command = request.model_dump(exclude={'lease_id'})
            with self.lock:
                if not self.motion.valid_lease(request.lease_id):
                    raise HTTPException(409, 'lease expired or invalid')
                saved = self.motion.feedback.get(request.command_id)
                if saved is not None:
                    if any(saved[k] != v for k, v in command.items()):
                        raise HTTPException(409, 'command ID already used')
                    return dict(saved, run_id=run_id)
                if request.type != 'stop' and not max(1, self.seq - 10) <= request.observation_seq <= self.seq:
                    raise HTTPException(409, 'observation sequence is stale or unknown')
                pose = self.latest_pose()
                active = self.motion.active
            if request.type == 'navigate_to_pose':
                command['path'] = self.clearance.route(pose[:2], (request.x, request.y)) if pose else None
            with self.lock:
                if not self.motion.valid_lease(request.lease_id):
                    raise HTTPException(409, 'lease expired or invalid')
                if request.type == 'navigate_to_pose' and active is not self.motion.active:
                    raise HTTPException(409, 'motion changed while planning')
                entry = self.motion.submit(command, self.latest_pose())
                return dict(entry, run_id=run_id)

        @app.get(prefix + '/commands/{command_id}')
        def feedback(run_id: str, command_id: str, authorization: str | None = Header(None)):
            authenticate(run_id, authorization)
            with self.lock:
                entry = self.motion.feedback.get(command_id)
                if entry is None:
                    raise HTTPException(404, 'command not found')
                return dict(entry, run_id=run_id)
        return app


def serve(path, port, particle_publisher=None):
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from nav_msgs.msg import Odometry
    from rosgraph_msgs.msg import Clock
    from sensor_msgs.msg import CameraInfo, Image
    from rclpy.clock import Clock as TimerClock
    from rclpy.clock import ClockType
    from rclpy.qos import qos_profile_sensor_data

    runtime = Runtime(json.loads(path.read_text()))
    if runtime.hazard_visuals and particle_publisher is None:
        raise ValueError('The prepared ignition run requires the Gazebo particle publisher')
    rclpy.init()
    node = rclpy.create_node('rescuetwin_policy_gateway')
    output = node.create_publisher(TwistStamped, '/rescuetwin/guarded_velocity', 10)

    def clock(message):
        current = message.clock.sec * 1_000_000_000 + message.clock.nanosec
        with runtime.lock:
            runtime.observe_clock(current)

    def pose(message):
        xyz, rotation = message.pose.pose.position, message.pose.pose.orientation
        with runtime.lock:
            runtime.pose = (xyz.x, xyz.y, yaw(rotation))
            runtime.pose_ns, runtime.pose_at = stamp(message.header), time.monotonic()

    def odom(message):
        with runtime.lock:
            runtime.twist = (message.twist.twist.linear.x, message.twist.twist.angular.z)
            runtime.twist_ns, runtime.twist_at = stamp(message.header), time.monotonic()

    def tick():
        with runtime.lock:
            runtime.seq += 1
            linear, angular = runtime.motion.tick(runtime.latest_pose(), runtime.twist or (0., 0.))
            updated = runtime.advance_hazards()
            runtime.update_samples()
            if updated and publisher:
                publisher.submit(runtime.particle_layout.snapshot(runtime.hazards))
        message = TwistStamped()
        message.header.stamp = node.get_clock().now().to_msg()
        message.twist.linear.x, message.twist.angular.z = float(linear), float(angular)
        output.publish(message)

    node.create_subscription(Clock, '/clock', clock, qos_profile_sensor_data)
    node.create_subscription(Odometry, '/rescuetwin/internal_pose', pose, qos_profile_sensor_data)
    node.create_subscription(Odometry, '/odom', odom, qos_profile_sensor_data)
    for key, topic, message in [('rgb', 'image', Image), ('depth', 'depth_image', Image),
                                ('info', 'camera_info', CameraInfo)]:
        node.create_subscription(message, '/rescuetwin/rgbd/' + topic,
                                 lambda msg, key=key: runtime.camera(key, msg), qos_profile_sensor_data)
    node.create_timer(.05, tick, clock=TimerClock(clock_type=ClockType.STEADY_TIME))
    descriptor = path.with_name('policy-client.json')
    server = uvicorn.Server(uvicorn.Config(runtime.app(), host='127.0.0.1', port=port,
                                            access_log=False))

    def run_ros():
        try:
            rclpy.spin(node)
        except rclpy.executors.ExternalShutdownException:
            pass
        finally:
            server.should_exit = True

    thread = threading.Thread(target=run_ros, daemon=True)
    fd = os.open(descriptor, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(dict(url=f'http://127.0.0.1:{port}/policy/v1/runs/{runtime.run_id}',
                       token=runtime.token), file)
    publisher = None
    try:
        if runtime.hazard_visuals:
            publisher = ParticlePublisher(particle_publisher)
        thread.start()
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.try_shutdown()
        if thread.is_alive():
            thread.join(timeout=2)
        if publisher:
            publisher.close()
        descriptor.unlink(missing_ok=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--particle-publisher', type=Path)
    args = parser.parse_args()
    serve(args.config, args.port, args.particle_publisher)


if __name__ == '__main__':
    main()
