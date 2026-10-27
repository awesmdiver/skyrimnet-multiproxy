#!/usr/bin/env bash
# Starts MultiProxy with its tray icon where a tray exists (otherwise it just runs the server). A second run opens the dashboard.
cd "$(dirname "$0")" || exit 1
python3 -c "import pystray, PIL" 2>/dev/null || python3 -m pip install -r requirements-tray.txt >/dev/null 2>&1
echo "Starting MultiProxy. Look for the icon by your clock."
nohup python3 multiproxy_tray.py >/dev/null 2>&1 &
