"""Continuous motion checks and the default pose follower for the policy runtime."""
import heapq
import itertools
import math
import time
import uuid

SPEED = .15
TURN = 1.
RADIUS = .15
STEP = .05
LEASE_SECONDS = 1.
VELOCITY_SECONDS = .25


def angle(value):
    return math.remainder(value, 2 * math.pi)


class Clearance:
    def __init__(self, scene):
        self.scene = scene

    def clear(self, x, y):
        s = self.scene
        ox, oy = s.origin
        r = s.resolution
        if not (ox + RADIUS < x < ox + s.width * r - RADIUS
                and oy + RADIUS < y < oy + s.height * r - RADIUS):
            return False
        lo = s.cell(x - RADIUS, y - RADIUS)
        hi = s.cell(x + RADIUS, y + RADIUS)
        for col in range(lo[0], hi[0] + 1):
            for row in range(lo[1], hi[1] + 1):
                if (col, row) in s.walls:
                    dx = max(ox + col * r - x, x - (ox + (col + 1) * r), 0)
                    dy = max(oy + row * r - y, y - (oy + (row + 1) * r), 0)
                    if math.hypot(dx, dy) <= RADIUS:
                        return False
        return True

    def segment(self, start, end):
        steps = max(1, math.ceil(math.dist(start, end) / .01))
        return all(self.clear(*(a + (b - a) * i / steps for a, b in zip(start, end)))
                   for i in range(steps + 1))

    def stopping_arc(self, pose, velocity, measured):
        x, y, yaw = pose
        # Check both the requested trajectory and residual motion before braking.
        for v, w in (velocity, measured):
            horizon = .25 + abs(v) / .5
            steps = max(1, math.ceil(horizon / .04))
            for index in range(steps + 1):
                t = horizon * index / steps
                if abs(w) < 1e-6:
                    point = x + v * t * math.cos(yaw), y + v * t * math.sin(yaw)
                else:
                    point = (x + v / w * (math.sin(yaw + w*t) - math.sin(yaw)),
                             y + v / w * (math.cos(yaw) - math.cos(yaw + w*t)))
                if not self.clear(*point):
                    return False
        return True

    def route(self, start, goal):
        if not self.clear(*start) or not self.clear(*goal):
            return None
        if self.segment(start, goal):
            return [goal]
        origin = self.scene.origin

        def point(cell):
            return tuple(o + (v + .5) * STEP for o, v in zip(origin, cell))

        def cell(p):
            return tuple(math.floor((v - o) / STEP) for o, v in zip(origin, p))

        def anchor(p):
            cx, cy = cell(p)
            neighbors = [(cx+i, cy+j) for i, j in itertools.product(range(-1, 2), repeat=2)]
            return next((q for q in sorted(neighbors, key=lambda q: math.dist(p, point(q)))
                         if self.segment(p, point(q))), None)

        source, target = anchor(start), anchor(goal)
        if source is None or target is None:
            return None
        queue, scores, parent = [(0., 0., source)], {source: 0.}, {}
        offsets = tuple(d for d in itertools.product((-1, 0, 1), repeat=2) if any(d))
        while queue:
            _, cost, current = heapq.heappop(queue)
            if cost != scores[current]:
                continue
            if current == target:
                path = [goal, point(current)]
                while current in parent:
                    current = parent[current]
                    path.append(point(current))
                path.reverse()
                simplified = []
                previous = start
                for index, waypoint in enumerate(path):
                    if index + 1 == len(path) or not self.segment(previous, path[index+1]):
                        simplified.append(waypoint)
                        previous = waypoint
                return simplified
            for dx, dy in offsets:
                nxt = current[0] + dx, current[1] + dy
                candidate = cost + math.hypot(dx, dy)
                if candidate >= scores.get(nxt, math.inf) or not self.segment(point(current), point(nxt)):
                    continue
                scores[nxt], parent[nxt] = candidate, current
                heapq.heappush(queue, (candidate + math.dist(nxt, target), candidate, nxt))
        return None


class Motion:
    """State machine. The caller serializes commands, observations and control ticks."""
    def __init__(self, clearance, now=time.monotonic):
        self.clearance, self.now = clearance, now
        self.lease = None
        self.lease_until = 0.
        self.active = None
        self.feedback = {}
        self.applied = (0., 0.)
        self.switch_stop = False

    def valid_lease(self, lease):
        return lease is not None and lease == self.lease and self.now() < self.lease_until

    def renew(self, lease):
        if lease is not None and not self.valid_lease(lease):
            return None
        if lease is None:
            if self.valid_lease(self.lease):
                return None
            self.finish('expired', 'lease_expired')
            self.lease = uuid.uuid4().hex
        self.lease_until = self.now() + LEASE_SECONDS
        return self.lease

    def finish(self, status, reason):
        if self.active:
            self.active.update(status=status, reason=reason, applied_velocity=[0., 0.])
            self.active = None
        self.applied = (0., 0.)
        return self.applied

    def submit(self, command, pose):
        key = command['command_id']
        if key in self.feedback:
            return self.feedback[key]
        kind = command['type']
        entry = dict(command, status='accepted', reason=None, requested_velocity=None,
                     applied_velocity=[0., 0.])
        path = entry.pop('path', None)
        self.feedback[key] = entry
        while len(self.feedback) > 2048:
            oldest = next(iter(self.feedback))
            if self.feedback[oldest] is self.active:
                self.feedback[oldest] = self.feedback.pop(oldest)
            else:
                self.feedback.pop(oldest)
        if kind == 'stop':
            self.finish('cancelled', 'stop_requested')
            entry.update(status='stopped', reason='stop_requested', requested_velocity=[0., 0.])
            self.switch_stop = True
            return entry
        if pose is None:
            entry.update(status='rejected', reason='localization_unavailable')
            return entry
        if kind == 'navigate_to_pose' and path is None:
            entry.update(status='rejected', reason='unreachable_goal')
            return entry
        switched = self.active is not None and self.active['type'] != kind
        self.finish('cancelled', 'superseded')
        self.switch_stop |= switched
        self.active = entry
        self.expires = self.now() + VELOCITY_SECONDS
        self.path = path
        if kind == 'velocity':
            entry['requested_velocity'] = [entry['linear_mps'], entry['angular_radps']]
        return entry

    def tick(self, pose, measured):
        if not self.active:
            return self.applied
        if not self.valid_lease(self.lease):
            return self.finish('expired', 'lease_expired')
        entry = self.active
        if entry['type'] == 'velocity' and self.now() >= self.expires:
            return self.finish('expired', 'command_expired')
        if pose is None:
            return self.finish('blocked', 'stale_localization_or_clock')
        if self.switch_stop:
            self.switch_stop = False
            return (0., 0.)
        if entry['type'] == 'velocity':
            desired = tuple(entry['requested_velocity'])
        else:
            position = pose[:2]
            while self.path and math.dist(position, self.path[0]) < .045:
                self.path.pop(0)
            if not self.path:
                error = angle(entry['yaw'] - pose[2])
                if abs(error) <= .09:
                    return self.finish('succeeded', 'goal_reached')
                desired = 0., max(-TURN, min(TURN, 2 * error))
            else:
                point = self.path[0]
                error = angle(math.atan2(point[1] - pose[1], point[0] - pose[0]) - pose[2])
                linear = 0. if abs(error) > .22 else min(SPEED, 1.5 * math.dist(position, point))
                desired = linear, max(-TURN, min(TURN, 2 * error))
        entry['requested_velocity'] = list(desired)
        if not self.clearance.stopping_arc(pose, desired, measured):
            return self.finish('blocked', 'predicted_collision')
        self.applied = desired
        entry.update(status='active', applied_velocity=list(desired))
        return desired
