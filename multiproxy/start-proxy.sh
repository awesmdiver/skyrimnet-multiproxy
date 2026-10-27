#!/usr/bin/env bash
# Start MultiProxy on Linux (the Linux twin of start-proxy.bat). Run it from anywhere.
#
#   ./start-proxy.sh
#
# Uses the private Python environment next to this file (.venv) if the model-server installer made
# one, otherwise your system python3. Stop it with Ctrl+C (or `systemctl --user stop multiproxy-model-server`
# if the installer set it up as a service): either way it stops its model engines too.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "Couldn't find Python 3. Please install it first," >&2
    echo "then run this again." >&2
    exit 1
fi

exec "$PY" multiproxy.py "$@"
