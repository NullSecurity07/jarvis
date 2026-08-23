#!/bin/bash
# jarvis: move things on your screen with your bare hands.
# Copyright (C) 2026 Jared Rhodenizer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
# board.sh — your AI's HANDS on the board. POSTs one JSON command to the
# jarvis server's /cmd channel (localhost only — this can reach nothing
# else). The server enforces its own action allowlist and the media-airlock
# jail, so this is safe to hand to an AI assistant.
#
# Usage:
#   board.sh '{"a":"add_card","title":"HELLO","body":"first card"}'
#   board.sh '{"a":"add_img","src":"misc/logo.png"}'
#   board.sh '{"a":"hand","src":"models/car.glb"}'     # deliver to reach
#   board.sh '{"a":"explode"}'                          # part the model
#   board.sh '{"a":"reset"}'                            # ring center stage
#   board.sh --wait 3000 '{"a":"present","title":"X"}' # block until the
#                                                       # board ACKS (ms)
#
# Prints ONE line: "landed #12 (board live)" when the tracker applied it,
# "queued #12 (board stale 4.0s — window hidden?)" when it is waiting in
# the queue, "rejected: ..." on a 400. Exit 0 landed/queued, 1 rejected,
# 2 server unreachable. Default wait is 1500 ms (0 = fire and forget).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=$(python3 -c "import json;print(json.load(open('$DIR/jarvis.json')).get('port',8794))" 2>/dev/null || echo 8794)
WAIT=1500
if [ "${1:-}" = "--wait" ]; then WAIT="${2:-1500}"; shift 2; fi
JSON="${1:-}"
if [ -z "$JSON" ]; then
    echo 'usage: board.sh [--wait ms] <json-command>' >&2
    exit 1
fi
RESP=$(curl -sS --max-time $(( WAIT / 1000 + 6 )) -X POST "http://127.0.0.1:$PORT/cmd?wait=$WAIT" \
    -H "Content-Type: application/json" -d "$JSON" -w '\n%{http_code}') || { echo "server unreachable on :$PORT (is jarvis running? ./launch.sh)"; exit 2; }
CODE="${RESP##*$'\n'}"
BODY="${RESP%$'\n'*}"
python3 - "$CODE" "$BODY" <<'PY'
import json, sys
code, body = sys.argv[1], sys.argv[2]
try: r = json.loads(body)
except Exception: r = {}
if code != "200":
    print(str(r.get("err", "rejected: " + (body or code)))); sys.exit(1)
age = r.get("age"); board = r.get("board")
where = f"board {board}" + (f" {age}s" if age is not None and board != "live" else "")
if board == "stale": where += " — window hidden/occluded?"
if board == "down": where += " — no tracker page open"
print(("landed" if r.get("landed") else "queued") + f" #{r.get('id')} ({where})")
PY
