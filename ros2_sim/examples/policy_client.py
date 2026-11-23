#!/usr/bin/env python3
"""Run a pose goal or a short custom velocity policy over HTTP."""
import argparse
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
import urllib.request
import uuid


DEFAULT_DESCRIPTOR = Path(__file__).resolve().parents[2] / 'data/ros2_sim/launcher/policy-client.json'


class PolicyError(Exception):
    pass


class Client:
    def __init__(self, descriptor):
        self.url, self.token = descriptor['url'], descriptor['token']
        self.lease = None

    @classmethod
    def from_descriptor(cls, path=None):
        descriptor_path = DEFAULT_DESCRIPTOR if path is None else Path(path)
        try:
            descriptor = json.loads(descriptor_path.read_text())
            url, token = descriptor['url'], descriptor['token']
            if not isinstance(url, str) or not isinstance(token, str) or not token:
                raise ValueError
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                raise ValueError
        except FileNotFoundError:
            if path is None:
                raise PolicyError(
                    'No active Gazebo run. Launch with ./setup.sh --gazebo and wait for it to report ready.'
                ) from None
            raise PolicyError(f'Policy client file not found at {descriptor_path}.') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise PolicyError(f'Could not read a valid policy client file at {descriptor_path}.') from None
        return cls(descriptor)

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

    def acquire(self):
        deadline = time.monotonic() + 1.2
        while True:
            try:
                self.renew()
                return
            except HTTPError as error:
                if error.code != 409:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PolicyError('The motion lease is busy. Stop the other policy and try again.') from None
                time.sleep(min(.1, remaining))

    def command(self, kind, observation, **values):
        return self.request('/commands', dict(type=kind, command_id=uuid.uuid4().hex,
                            lease_id=self.lease, observation_seq=observation['observation_seq'],
                            **values))


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, help='Policy client file (defaults to the active Gazebo setup run)')
    modes = parser.add_subparsers(dest='mode', required=True)
    pose = modes.add_parser('pose')
    for key in ('x', 'y', 'yaw'):
        pose.add_argument(key, type=float)
    velocity = modes.add_parser('velocity')
    for key in ('linear', 'angular', 'seconds'):
        velocity.add_argument(key, type=float)
    # Accept the original descriptor-first invocation as well.
    if len(argv) >= 2 and not argv[0].startswith('-') and argv[1] in ('pose', 'velocity'):
        argv = ['--client', *argv]
    args = parser.parse_args(argv)
    if args.mode == 'velocity' and (not math.isfinite(args.seconds) or args.seconds <= 0):
        velocity.error('seconds must be finite and greater than zero')
    return args


def run(client, args):
    deadline = time.monotonic() + 10
    observation = client.request('/observations')
    while observation['pose'] is None and time.monotonic() < deadline:
        time.sleep(.1)
        observation = client.request('/observations')
    if observation['pose'] is None:
        raise PolicyError('Robot localization is unavailable. Check that Gazebo is running and unpaused.')
    print('Starting pose:', observation['pose'], 'hazards:', observation['hazard'],
          'camera:', observation['rgbd']['status'])
    try:
        client.acquire()
        if args.mode == 'pose':
            observation = client.request('/observations')
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
                if result['status'] not in ('accepted', 'active'):
                    break
            print(result)
        return 0 if result and result['status'] in ('active', 'succeeded') else 1
    finally:
        if client.lease is not None:
            try:
                client.command('stop', observation)
            except OSError:
                # The gateway stops on lease expiry if the client loses transport.
                pass


def main():
    args = parse_args(sys.argv[1:])
    try:
        return run(Client.from_descriptor(args.client), args)
    except KeyboardInterrupt:
        return 130
    except PolicyError as error:
        print(error, file=sys.stderr)
    except HTTPError as error:
        if error.code in (401, 404):
            message = 'The policy run or command is no longer available. Check the active run or client file.'
        elif error.code == 409:
            message = 'The motion lease or observation expired. Try the command again.'
        elif error.code == 422:
            message = 'The policy rejected the command. Check the arguments and velocity limits.'
        else:
            message = f'The policy gateway returned HTTP {error.code}.'
        print(message, file=sys.stderr)
    except OSError:
        print('Could not reach the policy gateway. Check that Gazebo is running and the client file is current.',
              file=sys.stderr)
    except (ValueError, KeyError, TypeError):
        print('The policy gateway returned an invalid response.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
