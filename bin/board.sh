#!/bin/bash
# board.sh — your AI's HANDS on the board. POSTs one JSON command to the
# barehands server's /cmd channel (localhost only — this can reach
# nothing else). The server enforces its own action allowlist and the
# media-airlock jail, so this is safe to hand to an AI assistant.
#
# Usage:
#   board.sh '{"a":"add_card","title":"HELLO","body":"first card"}'
#   board.sh '{"a":"add_img","src":"misc/logo.png"}'
#   board.sh '{"a":"hand","src":"models/car.glb"}'     # deliver to reach
#   board.sh '{"a":"explode"}'                          # part the model
#   board.sh '{"a":"reset"}'                            # ring center stage
#
# Prints the HTTP code: 204 = the board took it, 400 = rejected.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=$(python3 -c "import json;print(json.load(open('$DIR/barehands.json')).get('port',8794))" 2>/dev/null || echo 8794)
JSON="${1:-}"
if [ -z "$JSON" ]; then
    echo 'usage: board.sh <json-command>' >&2
    exit 1
fi
curl -sS --max-time 5 -X POST "http://127.0.0.1:$PORT/cmd" \
    -H "Content-Type: application/json" \
    -d "$JSON" -o /dev/null -w "%{http_code}\n"
