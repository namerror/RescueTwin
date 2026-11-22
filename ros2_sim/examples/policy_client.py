#!/usr/bin/env python3
"""Run a pose goal or a short custom velocity policy over HTTP."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import uuid


class Client:
    def __init__(self, descriptor):
        self.url, self.token = descriptor['url'], descriptor['token']
        self.lease = None

    def request(self, route, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url + route, data=body,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)

    def renew(self):
        self.lease = self.request('/lease', {'lease_id': self.lease})['lease_id']

    def command(self, kind, observation, **values):
        return self.request('/commands', dict(type=kind, command_id=uuid.uuid4().hex,
                            lease_id=self.lease, observation_seq=observation['observation_seq'],
                            **values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('descriptor', type=Path)
    modes = parser.add_subparsers(dest='mode', required=True)
    pose = modes.add_parser('pose')
    for key in ('x', 'y', 'yaw'):
        pose.add_argument(key, type=float)
    velocity = modes.add_parser('velocity')
    for key in ('linear', 'angular', 'seconds'):
        velocity.add_argument(key, type=float)
    args = parser.parse_args()
    client = Client(json.loads(args.descriptor.read_text()))
    deadline = time.monotonic() + 10
    observation = client.request('/observations')
    while observation['pose'] is None and time.monotonic() < deadline:
        time.sleep(.1)
        observation = client.request('/observations')
    if observation['pose'] is None:
        raise SystemExit('Localization is unavailable')
    print('Starting pose:', observation['pose'], 'hazards:', observation['hazard'],
          'camera:', observation['rgbd']['status'])
    client.renew()
    try:
        if args.mode == 'pose':
            result = client.command('navigate_to_pose', observation, x=args.x, y=args.y, yaw=args.yaw)
            while result['status'] in ('accepted', 'active'):
                time.sleep(.15)
                client.renew()
                result = client.request('/commands/' + result['command_id'])
            print(result)
        else:
            deadline = time.monotonic() + args.seconds
            result = None
            while time.monotonic() < deadline:
                client.renew()
                observation = client.request('/observations')
                result = client.command('velocity', observation,
                                        linear_mps=args.linear, angular_radps=args.angular)
                time.sleep(.1)
                result = client.request('/commands/' + result['command_id'])
                if result['status'] == 'blocked':
                    break
            print(result)
    finally:
        try:
            client.command('stop', observation)
        except OSError:
            # The gateway stops on lease expiry if the client loses transport.
            pass


if __name__ == '__main__':
    main()
