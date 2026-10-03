#!/usr/bin/env bash
# Starts the live SUMO server and opens the 3D city in your browser.
# Requires Python 3.14 with sumo/requirements.txt installed.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found on your PATH."
  echo "Install Python 3.14, then run: pip install -r requirements.txt"
  exit 1
fi

python3 -u server/server.py &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

sleep 12
URL="http://localhost:8765/"
if command -v open >/dev/null 2>&1; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
fi

wait $SERVER_PID