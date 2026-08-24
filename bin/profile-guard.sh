#!/bin/bash
# ONE BOARD PROCESS AT A TIME — sourced by every launcher.
#
# WHY THIS REFUSES INSTEAD OF WARNING. Chrome command-line flags land on a
# genuinely NEW browser process. Open a second --app window against a
# user-data-dir that a Chrome process already owns and it is handed to that
# existing process, which keeps ITS flags; yours are silently discarded.
# Measured cost of hitting this, same machine, same board, same models:
#
#   own process   hands  8.2 ms   face 10.4 ms   pose 13.3 ms   31 fps
#   second window hands 30-55 ms  face 56-63 ms  pose 53-59 ms   8-10 fps
#
# --ignore-gpu-blocklist goes missing, MediaPipe drops to the XNNPACK CPU
# delegate, and every model runs 4-6x slower. This cost an entire task's
# analysis once: a report concluded "pose is too expensive, throttle it"
# from numbers taken in a flag-dropped window. Nothing was wrong with pose.
# The no-throttle flags vanish the same way, which is the difference
# between the board holding 31 fps while hidden and sagging to 12-15.
#
# The profile is deliberately SHARED between the board, HUD and strip
# modes: camera permission is per-profile, and a fresh profile would make
# you grant it again every time. They are alternative modes, so only one
# should ever be up. Set JARVIS_FORCE=1 to launch anyway (you will get a
# slow board -- there is no situation where that is what you want, but it
# is your machine).

# board_profile_busy <profile-dir> -> 0 if a Chrome process owns it
board_profile_busy() {
  local prof="$1" lock pid
  # Chrome's own answer first: SingletonLock is a symlink whose target
  # ends in -<pid> of the process holding the profile. More reliable than
  # matching command lines, and it cannot match this script itself.
  lock="$prof/SingletonLock"
  if [ -L "$lock" ]; then
    pid="$(readlink "$lock" 2>/dev/null)"; pid="${pid##*-}"
    case "$pid" in
      ''|*[!0-9]*) ;;
      *) if kill -0 "$pid" 2>/dev/null; then BOARD_BUSY_PID="$pid"; return 0; fi ;;
    esac
  fi
  # Backstop: a live chrome whose argv names this profile. Restricted to
  # the chrome binary so a shell that merely mentions the path (this
  # script's own caller, a grep, an editor) can never match -- the
  # pgrep-matches-my-own-shell trap has bitten this project before.
  pid="$(pgrep -f -- "^[^ ]*chrome[^ ]* .*--user-data-dir=$prof" 2>/dev/null | head -1)"
  if [ -n "$pid" ]; then BOARD_BUSY_PID="$pid"; return 0; fi
  return 1
}

# board_profile_require_free <profile-dir> <launcher-name>
# Refuses (exit 1) if the profile is busy, unless JARVIS_FORCE=1.
board_profile_require_free() {
  local prof="$1" who="${2:-this launcher}"
  board_profile_busy "$prof" || return 0
  if [ "${JARVIS_FORCE:-}" = "1" ]; then
    printf '[%s] JARVIS_FORCE=1: launching a SECOND window on a busy profile.\n' "$who" >&2
    printf '[%s] Expect ~8-10 fps instead of ~31 — the GPU delegate will be lost.\n' "$who" >&2
    return 0
  fi
  cat >&2 <<MSG
[$who] REFUSING: a jarvis Chrome is already running (pid $BOARD_BUSY_PID).

  A second window on this profile does not get these flags — it is handed
  to the running browser, which keeps its own. You would silently lose the
  GPU delegate and the no-throttle flags: measured 8-10 fps instead of 31,
  with every model running 4-6x slower.

  Close the board that is already open, then run this again:
      kill $BOARD_BUSY_PID
  Or, if you really want a second window anyway:
      JARVIS_FORCE=1 $0
MSG
  exit 1
}
