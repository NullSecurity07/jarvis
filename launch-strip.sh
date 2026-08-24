#!/bin/bash
# jarvis STRIP — the board as a small, always-visible panel instead of a
# wall. WHY THIS EXISTS: Chrome on this machine will not give a per-pixel
# transparent window (tested with and without --enable-transparent-visuals,
# with and without the GPU: the page's background always composites opaque
# white), so a full-screen see-through HUD is impossible here. And a
# fullscreen board hidden on another workspace keeps tracking but drops to
# ~12-15 fps, which makes hand-driven pointer control feel laggy.
#
# A small window that is MAPPED and sticky solves both: it stays on every
# workspace, so it never stops being composited (full frame rate), it does
# not cover your work, and you can still see your hands in the self-view.
# Your real apps stay full size underneath. F fullscreens the strip when
# you want the whole glass; F again brings it back.
set -u
cd "$(dirname "$(readlink -f "$0")")"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
export DISPLAY="${DISPLAY:-:0}"
[ -n "${XAUTHORITY:-}" ] || export XAUTHORITY=/run/user/$(id -u)/lyxauth

say()  { printf '[launch-strip] %s\n' "$*"; }
fail() { printf '[launch-strip] ERROR: %s\n' "$*" >&2; exit 1; }

# geometry: a corner panel, tunable here or with the env vars
W="${JARVIS_STRIP_W:-460}"; H="${JARVIS_STRIP_H:-330}"
GAP="${JARVIS_STRIP_GAP:-16}"
SCREEN_W=$(xdotool getdisplaygeometry | cut -d' ' -f1)
SCREEN_H=$(xdotool getdisplaygeometry | cut -d' ' -f2)
X=$(( SCREEN_W - W - GAP )); Y=$(( SCREEN_H - H - GAP ))

PORT=$(python3 -c "import json;print(json.load(open('jarvis.json')).get('port',8794))" 2>/dev/null || echo 8794)
if ! curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null; then
  say "starting the server…"
  nohup python3 server.py >> launcher.log 2>&1 &
  for _ in $(seq 1 24); do curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null && break; sleep 0.25; done
fi
curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null || fail "server did not come up on :$PORT — see launcher.log"
say "server is up on :$PORT"

if pgrep -f "user-data-dir=$HOME/.local/share/jarvis/chrome" >/dev/null 2>&1; then
  say "NOTE: a jarvis Chrome window is already open on this profile. A second"
  say "window inherits the first process's flags, so close the other board"
  say "first if the strip misbehaves."
fi

URL="http://127.0.0.1:$PORT/stage.html?res=1280x720&hud=1&strip=1"
PROFILE="$HOME/.local/share/jarvis/chrome"
mkdir -p "$PROFILE"
say "opening the strip (${W}x${H} at ${X},${Y})…"
google-chrome-stable \
  --user-data-dir="$PROFILE" \
  --class=jarvis-strip \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-background-timer-throttling \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --window-size="$W,$H" \
  --app="$URL" >/dev/null 2>&1 &
CPID=$!

XID=""
for _ in $(seq 1 40); do
  XID=$(i3-msg -t get_tree 2>/dev/null | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    p=(n.get('window_properties') or {})
    if n.get('window') and (p.get('class')=='jarvis-strip' or p.get('instance')=='jarvis-strip'): out.append(n['window'])
    for c in (n.get('nodes') or [])+(n.get('floating_nodes') or []): w(c)
w(t); print(out[0] if out else '')")
  [ -n "$XID" ] && break
  sleep 0.4
done
[ -n "$XID" ] || fail "the strip window never appeared (chrome pid $CPID)"
say "window found: xid $XID"

# floating + sticky = present on every workspace and always mapped, which is
# what keeps the tracker at full frame rate; no border so it reads as a panel
i3-msg -q "[id=$XID] floating enable, border none, sticky enable, resize set ${W} ${H}, move position ${X} ${Y}" \
  || say "WARNING: i3 could not float/stick the window — it will still work, just not follow you across workspaces"
say "strip floated, stuck to every workspace, parked bottom-right"
say ""
say "POINT (index up, hold ~0.7s) to take the mouse · FIST to give it back · Esc always releases"
say "M cycles the self-view brightness · F fullscreens the board and back"
