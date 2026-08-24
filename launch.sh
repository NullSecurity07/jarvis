#!/bin/bash
# jarvis — THE launcher. There is exactly one, on purpose.
#
# It opens the board FULL SCREEN and CLICK-THROUGH: the glass covers the
# display, your hands drive the real mouse, and every click passes through
# to whatever is underneath. The camera mirror lives behind the glass —
# M dials it from full-strength down to off, so you choose how much of
# yourself you see while you work.
#
# Click-through is what makes controlling a desk possible at all. The
# board is opaque and on top, so a synthetic click at screen coordinates
# would otherwise land on the board itself instead of the app. Emptying
# the window's X input shape lets the event through while the glass keeps
# compositing above it.
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

PORT=$(python3 -c "
import json
try: print(json.load(open('jarvis.json')).get('port',8794))
except Exception: print(8794)" 2>/dev/null || echo 8794)
W=$(xdotool getdisplaygeometry | cut -d' ' -f1)
H=$(xdotool getdisplaygeometry | cut -d' ' -f2)

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
  say "the board is already open — focused it."
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

# mode=mirror  : the mirror view — the one that feels right to work in
# hud=1        : hands drive the REAL mouse (this flag, not the window shape,
#                is what enables pointer control)
URL="http://127.0.0.1:$PORT/stage.html?mode=mirror&hud=1"
say "opening the board full screen (${W}x${H})…"
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
[ -n "$XID" ] || fail "the board never appeared (chrome pid $CPID)"

# floating + sticky: present on every workspace and always mapped, which is
# what keeps the tracker at full frame rate. border none so no titlebar
# eats a pixel. NEVER `fullscreen enable` — that makes i3 unmap the tiled
# windows behind it, which are exactly the ones whose live frames the
# glass shows.
i3-msg -q "[id=$XID] floating enable, border none, sticky enable" \
  || say "WARNING: i3 would not float/stick the window; it still works, it just won't follow you across workspaces"

# Size comes from the server's `panel` verb rather than a second copy of
# the geometry here. Measured reason: a chained "floating enable, resize
# set, move position" issued this early left the window at the wrong size
# — i3 had not finished settling the just-mapped window — while the
# server's separate calls land reliably.
for _ in $(seq 1 12); do
  sleep 0.4
  curl -s -m 3 -X POST "http://127.0.0.1:$PORT/desks/cmd" \
    -H 'Content-Type: application/json' -d '{"a":"panel","mode":"full"}' \
    | grep -q '"ok"' && break
done

# CLICK-THROUGH. Without this the board eats every click — including the
# ones your hands aim into a desk card — because it is opaque and on top.
python3 tools/clickthrough.py "$XID" \
  || fail "could not make the board click-through; refusing to leave an opaque \
full-screen window that swallows every click. Close it (it has no titlebar: \
use your i3 kill binding) and check tools/clickthrough.py."

say "board up, full screen, click-through, on every workspace."
say ""
say "  POINT (index up, others folded, ~0.7s)  take the mouse"
say "  FIST                                    give it back   ·  Esc always releases"
say "  M  mirror: full -> 60% -> 35% -> 20% -> off      D  debug overlay"
say ""
say "TO CONTROL A DESK: open the Desks orb, tap a window to stage it as a"
say "live card. Then POINT to arm — the card you are aiming at glows — and"
say "your finger drives that app's real cursor. Pinch to click inside it."
say "Disarm (fist) and pinch goes back to moving the card around."
