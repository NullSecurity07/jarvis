#!/bin/bash
# J4RVIS launcher — starts the barehands server if it's not up, then opens
# the board in Chrome at the resolution tuned for this laptop's camera
# (1280x720 native max = 30fps MJPG; the 1080p default would negotiate).
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
cd "$(dirname "$(readlink -f "$0")")"

# camera: the page owns it now. THE LIGHT GOVERNOR in stage.html sets
# the exposure state live through the server's /cam bridge (v4l2-ctl):
# FAST = auto exposure with dynamic framerate OFF -> a solid 30fps, the
# detector's input lifted in software + the ISP tone ladder (gamma /
# brightness / backlight comp) walked up in the dark and down in a lit
# room; LIGHT = dynamic framerate ON (the old 17fps light-buying config)
# only when the hand zone is too dark to lift. Measured 2026-08-23: this
# camera has NO auto-gain, so 30fps = half the light, and ROI metering /
# manual exposure change nothing — see the comment block in stage.html.
# L on the board cycles auto/fast/light; ?fps=30|auto|light pins it;
# K runs the camera clinic; GET /state -> perf.cam is the telemetry.
# (Chrome does not reset V4L2 controls on open, so nothing is needed
# here before launch; the governor re-applies on every camera start.)

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
