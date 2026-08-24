#!/bin/bash
# jarvis — THE launcher. There is exactly one, on purpose.
#
# It opens the board as a small mirror panel: you see yourself, your hands
# and the ring, it sits stuck to the corner of every workspace, and it
# never covers what you are working on. Your hands drive the real mouse
# from inside it, and live views of your own windows can be staged on the
# glass. Press F to grow it to full screen for a moment and F again to put
# it back — that is a resize, not a second way to run the thing.
#
# There used to be three launchers (plain board / HUD / strip). They were
# three window shapes around one page, and the transparent full-screen
# "HUD" could never work here anyway: Chrome on this machine will not give
# a per-pixel transparent window (tested with and without
# --enable-transparent-visuals, with and without the GPU — always opaque
# white). So: one launcher, one surface.
set -u
cd "$(dirname "$(readlink -f "$0")")"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
export DISPLAY="${DISPLAY:-:0}"
[ -n "${XAUTHORITY:-}" ] || export XAUTHORITY=/run/user/$(id -u)/lyxauth

say()  { printf '[jarvis] %s\n' "$*"; }
fail() { printf '[jarvis] ERROR: %s\n' "$*" >&2; exit 1; }

CFG() { python3 -c "
import json,sys
try: d=json.load(open('jarvis.json'))
except Exception: d={}
p=d.get('panel') or {}
print(d.get('port',8794), p.get('w',620), p.get('h',460), p.get('gap',16))" 2>/dev/null || echo "8794 620 460 16"; }
read -r PORT PW PH GAP <<<"$(CFG)"
W="${JARVIS_W:-$PW}"; H="${JARVIS_H:-$PH}"; GAP="${JARVIS_GAP:-$GAP}"

# ---- the server
if ! curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null; then
  say "starting the server…"
  nohup python3 server.py >> launcher.log 2>&1 &
  for _ in $(seq 1 24); do curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null && break; sleep 0.25; done
fi
curl -s -m 1 "http://127.0.0.1:$PORT/config" >/dev/null || fail "server did not come up on :$PORT — see launcher.log"
say "server up on :$PORT"

# ---- already running? focus it instead of opening a second one
EXIST=$(xdotool search --class jarvis-panel 2>/dev/null | head -1)
if [ -n "$EXIST" ]; then
  i3-msg -q "[id=$EXIST] focus"
  say "the panel is already open — focused it. (F grows/shrinks it.)"
  exit 0
fi

# ---- one Chrome process per profile, enforced. A second window on a busy
# profile is handed to the running browser and keeps ITS flags, silently
# losing the GPU delegate: measured 8-10 fps instead of 31. See
# bin/profile-guard.sh.
PROFILE="$HOME/.local/share/jarvis/chrome"
mkdir -p "$PROFILE"
. ./bin/profile-guard.sh
board_profile_require_free "$PROFILE" "jarvis"

SCREEN_W=$(xdotool getdisplaygeometry | cut -d' ' -f1)
SCREEN_H=$(xdotool getdisplaygeometry | cut -d' ' -f2)
X=$(( SCREEN_W - W - GAP )); Y=$(( SCREEN_H - H - GAP ))

# mode=mirror  : the mirror view — the one that feels right to work in
# hud=1        : hands drive the REAL mouse (this flag, not the window shape,
#                is what enables pointer control)
URL="http://127.0.0.1:$PORT/stage.html?mode=mirror&hud=1"
say "opening the panel (${W}x${H} at ${X},${Y})…"
google-chrome-stable \
  --user-data-dir="$PROFILE" \
  --class=jarvis-panel \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-background-timer-throttling \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --window-size="$W,$H" \
  --app="$URL" >/dev/null 2>&1 &
CPID=$!
# the flags above, and why each one earns its place:
#   backgrounding-occluded-windows / renderer-backgrounding / timer-throttling
#     — the tracker keeps full rate even when the panel is covered or its
#       workspace is hidden (without them it sagged to 12-15 fps)
#   ignore-gpu-blocklist — keeps MediaPipe on the GPU delegate; the CPU
#       fallback runs every model 4-6x slower
#   autoplay-policy — the foley can sound before the first click

XID=""
for _ in $(seq 1 40); do
  XID=$(xdotool search --class jarvis-panel 2>/dev/null | head -1)
  [ -n "$XID" ] && break
  kill -0 "$CPID" 2>/dev/null || fail "Chrome exited before the window appeared"
  sleep 0.4
done
[ -n "$XID" ] || fail "the panel never appeared (chrome pid $CPID)"

# floating + sticky: present on every workspace and always mapped, which is
# what keeps the tracker at full frame rate. border none so no titlebar
# eats a pixel. NEVER `fullscreen enable` — that makes i3 unmap the tiled
# windows behind it, which are exactly the ones whose live frames the
# glass shows.
i3-msg -q "[id=$XID] floating enable, border none, sticky enable" \
  || say "WARNING: i3 would not float/stick the window; it still works, it just won't follow you across workspaces"

# The SIZE and POSITION come from the server's `panel` verb, not from a
# second copy of the geometry here. Two reasons, one of them measured: a
# chained "floating enable, resize set, move position" issued this early
# left the window at full screen size — i3 had not finished settling the
# just-mapped window — while the server's separate calls land reliably.
# And F uses that same verb, so there is exactly one place that decides
# what "panel size" means.
for _ in $(seq 1 12); do
  sleep 0.4
  curl -s -m 3 -X POST "http://127.0.0.1:$PORT/desks/cmd" \
    -H 'Content-Type: application/json' -d '{"a":"panel","mode":"panel"}' \
    | grep -q '"ok"' && break
done

say "panel up, stuck to every workspace, bottom-right."
say ""
say "  POINT (index up, others folded, ~0.7s)  take the mouse"
say "  FIST                                    give it back   ·  Esc always releases"
say "  F  grow to full screen / back      M  mirror brightness      D  debug overlay"
say ""
say "Live windows: open the Desks orb, tap a window to stage it. One that is"
say "on another workspace says OFF-SCREEN — tap it to bring it here and it"
say "goes live."
