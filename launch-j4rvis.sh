#!/bin/bash
# J4RVIS launcher — starts the barehands server if it's not up, then opens
# the board in Chrome at the resolution tuned for this laptop's camera
# (1280x720 native max = 30fps MJPG; the 1080p default would negotiate).
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
cd "$(dirname "$(readlink -f "$0")")"

# camera: FINAL verdict after a day of A/B (2026-08-23). Auto exposure
# with dynamic framerate ON is the only config where pinch/gestures
# detect reliably in this backlit room. It runs ~17-18fps — that is the
# camera buying the light it needs to SEE the hands, and every gesture
# (pinch, throw, claw) was proven working at this rate. The 30fps manual
# configs all starved detection. Backlight comp + brightness are boosted
# to help the shadowed side. For real 30fps: put a lamp facing the user.
v4l2-ctl -d /dev/video0 -c auto_exposure=3 -c exposure_dynamic_framerate=1 \
  -c backlight_compensation=2 -c brightness=170 2>/dev/null

PORT=$(python3 -c "import json;print(json.load(open('barehands.json')).get('port',8794))" 2>/dev/null || echo 8794)
URL="http://127.0.0.1:$PORT/stage.html?res=1280x720"

if ! curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null; then
  nohup python3 server.py >> launcher.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null && break
    sleep 0.25
  done
fi

# idempotent: if a board window already exists, focus it; else open one
if i3-msg -t get_tree 2>/dev/null | grep -q '"name":"barehands"'; then
  i3-msg '[class="Google-chrome" title="^barehands$"] focus' >/dev/null 2>&1
  exit 0
fi
exec google-chrome-stable --app="$URL" >/dev/null 2>&1
