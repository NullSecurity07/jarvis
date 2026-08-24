#!/bin/bash
# jarvis launcher — THE HUD (Task 20): the glass floats over your REAL
# desktop and your hands drive the REAL system pointer. A sibling of
# launch.sh, not a replacement — that one mirrors nothing and never
# touches your pointer; this one is the "work inside my desktop" mode.
#
# Empirically verified on this machine (2026-08-24): a Chrome --app window
# launched with --enable-transparent-visuals over a picom compositor is
# GENUINELY see-through (confirmed by rendering it over a known solid
# color and reading the composited pixels back — they matched exactly).
# --disable-gpu / --disable-gpu-compositing are NOT needed for that and
# were tested and rejected: they still left the window solid white, and
# would have killed MediaPipe's GPU delegate (CPU fallback is ~51ms/frame
# vs 0.006ms/call for the XTest pointer — see server.py's THE POINTER).
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
cd "$(dirname "$(readlink -f "$0")")"

say() { echo "[launch-hud] $*"; }
fail() { echo "[launch-hud] FAILED: $*" >&2; exit 1; }

PORT=$(python3 -c "import json;print(json.load(open('jarvis.json')).get('port',8794))" 2>/dev/null || echo 8794)
URL="http://127.0.0.1:$PORT/stage.html?mode=overlay&hud=1"

if ! curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null; then
  say "server not up — starting it"
  nohup python3 server.py >> launcher.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null && break
    sleep 0.25
  done
  curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null || fail "server never came up — check launcher.log"
fi
say "server is up on :$PORT"

PTR_ENABLED=$(curl -s -m 2 "http://127.0.0.1:$PORT/config" | python3 -c "import json,sys;print(json.load(sys.stdin).get('pointer',{}).get('enabled',False))" 2>/dev/null)
if [ "$PTR_ENABLED" != "True" ]; then
  say "WARNING: jarvis.json \"pointer\":{\"enabled\":...} is not true — /pointer will 404 and hands will just hover, not drive the mouse. The glass still opens; add pointer.enabled:true to fix it."
fi

# idempotent: an already-open HUD window (same title jarvis.html always
# uses) gets focused instead of a second one piling on top
if i3-msg -t get_tree 2>/dev/null | grep -q '"name":"jarvis"'; then
  say "a jarvis window already exists — focusing it, not opening a second"
  i3-msg '[class="Google-chrome" title="^jarvis$"] focus' >/dev/null 2>&1
  exit 0
fi

# Its OWN profile, same one launch.sh uses (so the camera permission
# already granted there carries over — no second prompt) — but see the
# NOTE below: this only lands its flags if that profile has no Chrome
# process already running under it.
PROFILE="$HOME/.local/share/jarvis/chrome"
mkdir -p "$PROFILE"
if pgrep -f -- "--user-data-dir=$PROFILE" >/dev/null 2>&1; then
  say "NOTE: a Chrome process is already running under this profile (the"
  say "      plain board from launch.sh?). Chrome flags — INCLUDING THE"
  say "      TRANSPARENCY FLAG — only land on a genuinely NEW process; a"
  say "      second --app window under an already-running profile just"
  say "      opens inside it and would come out OPAQUE. Close that Chrome"
  say "      window first, then re-run this launcher."
fi

say "opening the HUD…"
google-chrome-stable \
  --user-data-dir="$PROFILE" \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-background-timer-throttling \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --enable-transparent-visuals \
  --app="$URL" >/dev/null 2>&1 &
CHROME_PID=$!

say "waiting for the window (pid $CHROME_PID)…"
XID=""
for _ in $(seq 1 40); do
  XID=$(xdotool search --name "^jarvis$" 2>/dev/null | head -1)
  [ -n "$XID" ] && break
  kill -0 "$CHROME_PID" 2>/dev/null || fail "Chrome exited before the window ever appeared — check that google-chrome-stable runs at all"
  sleep 0.25
done
[ -n "$XID" ] || fail "window never appeared (waited 10s) — is Chrome actually launching? try running the google-chrome-stable command above by hand"
say "window found: xid 0x$(printf '%x' "$XID")"

# THE FULL SCREEN, above the tiled layer, on every workspace: floating so
# it can sit on top of tiled windows at all, border none so no titlebar
# eats a pixel of the glass, sticky so switching workspaces never loses
# it, and an explicit move+resize to the real screen size — not
# `fullscreen enable`, which some i3 configs treat as a toggle with side
# effects on the previously-focused window.
i3-msg "[id=\"$XID\"] floating enable, border none, sticky enable, move position 0 0, resize set 1920 1080" >/dev/null 2>&1
say "window floated + fullscreened (1920x1080 @ 0,0), sticky across workspaces"

python3 tools/clickthrough.py "$XID" || fail "clickthrough.py could not make the window click-through — clicks would be stolen from your real desktop, so refusing to leave it up. Close it by hand (it has no titlebar: alt+click drag or i3's kill binding)."

say "HUD is up. POINTER STARTS DISARMED — press A, or POINT (index up)"
say "(index up, other fingers folded) for ~0.7s to arm it. Make a FIST"
say "to release it; Esc always disarms instantly. See README.md HUD section."
