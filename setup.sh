#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == --gazebo ]]; then
    shift
    exec python3 scripts/gazebo_setup.py "$@"
fi
exec python3 scripts/bringup.py "$@"
