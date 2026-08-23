#!/bin/bash
# jarvis launcher — starts the server if it's not up, then opens the board
# in Chrome. The camera resolution / fps defaults live in jarvis.json now
# ("res", "fps"); the page reads them from /config, so the URL stays bare.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
cd "$(dirname "$(readlink -f "$0")")"

PORT=$(python3 -c "import json;print(json.load(open('jarvis.json')).get('port',8794))" 2>/dev/null || echo 8794)
URL="http://127.0.0.1:$PORT/stage.html"

if ! curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null; then
  nohup python3 server.py >> launcher.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null && break
    sleep 0.25
  done
fi

# idempotent: if a board window already exists, focus it; else open one
if i3-msg -t get_tree 2>/dev/null | grep -q '"name":"jarvis"'; then
  i3-msg '[class="Google-chrome" title="^jarvis$"] focus' >/dev/null 2>&1
  exit 0
fi

# The board gets its OWN Chrome profile + process. Why: Chrome flags only
# apply to a NEW browser process — with the default profile they are
# silently ignored whenever any other Chrome window is already open. With
# its own profile the flags always land, and closing your browser never
# takes the board down with it. (First launch asks for the camera once.)
PROFILE="$HOME/.local/share/jarvis/chrome"
mkdir -p "$PROFILE"
exec google-chrome-stable \
  --user-data-dir="$PROFILE" \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-background-timer-throttling \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --app="$URL" >/dev/null 2>&1
# --disable-backgrounding-occluded-windows + renderer-backgrounding + timer
#   throttling: the tracker keeps running at full rate when the board sits
#   on another i3 workspace (before: Chrome paused it and /state went stale,
#   commands queued unseen)
# --ignore-gpu-blocklist: keep MediaPipe on the GPU delegate (the CPU
#   fallback is 51ms/frame; the stage's delegate watchdog stays as the net)
# --autoplay-policy: the foley (WebAudio) can sound before the first click
