#!/usr/bin/env python3
# jarvis — tools/clickthrough.py: make a window transparent to INPUT.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sets a window's INPUT shape to an empty region via the X11 Shape
extension, so every click, scroll and pointer event passes straight
through it to whatever renders underneath — while the window itself
keeps compositing on top exactly as before. Was used by the old
launch-hud.sh to
make the HUD's overlay window click-through: it must never steal a
click, scroll, or hover from the real desktop it floats above.

  tools/clickthrough.py <window-id>     # decimal or 0x-hex xid
  tools/clickthrough.py --title jarvis  # find the window by title first

Safe to run twice: XShapeCombineRectangles(..., ShapeSet, ...) with an
empty rectangle list REPLACES the input shape each time, so re-running it
on an already-clickthrough window is a no-op, not a second empty region
stacked on the first.
"""
import argparse
import ctypes
import ctypes.util
import subprocess
import sys

SHAPE_INPUT = 2   # ShapeInput
SHAPE_SET = 0     # ShapeSet
UNSORTED = 0      # Unsorted


def find_by_title(substr):
    """xdotool search --name <substr> -> the first matching window id, or
    None. First match wins (there is normally exactly one HUD window;
    if xdotool ever returns several, that's surfaced by the caller
    printing which id it acted on, not silently guessed away)."""
    try:
        r = subprocess.run(["xdotool", "search", "--name", substr],
                            capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    ids = [line for line in r.stdout.splitlines() if line.strip()]
    return int(ids[0]) if ids else None


def clickthrough(xid):
    """Set xid's input shape to empty. Returns (ok, detail_or_None). Same
    ctypes discipline as server.py's XTest bridge: restype/argtypes MUST
    be set before any call, or the display pointer truncates to 32 bits
    and this segfaults instead of failing cleanly."""
    try:
        x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
        xext = ctypes.CDLL(ctypes.util.find_library("Xext"))
    except Exception as e:
        return False, f"couldn't load libX11/libXext: {e}"
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xext.XShapeCombineRectangles.argtypes = [
        ctypes.c_void_p,   # Display*
        ctypes.c_ulong,    # Window dest
        ctypes.c_int,      # destKind (ShapeInput)
        ctypes.c_int,      # xOff
        ctypes.c_int,      # yOff
        ctypes.c_void_p,   # XRectangle* (NULL here)
        ctypes.c_int,      # nRects (0 here)
        ctypes.c_int,      # op (ShapeSet)
        ctypes.c_int,      # ordering (Unsorted)
    ]
    d = x11.XOpenDisplay(None)
    if not d:
        return False, "XOpenDisplay failed (no DISPLAY?)"
    try:
        xext.XShapeCombineRectangles(d, xid, SHAPE_INPUT, 0, 0, None, 0,
                                      SHAPE_SET, UNSORTED)
        x11.XFlush(d)
    except Exception as e:
        return False, f"XShapeCombineRectangles failed: {e}"
    return True, None


def still_renders(xid):
    """Best-effort confirmation the window is still alive and mapped —
    there is no portable command-line read-back of a window's CURRENT
    input shape, so this only proves the window survived the call, not
    that clicks actually pass through (confirm that by hand: click
    something under the HUD and watch it react)."""
    try:
        r = subprocess.run(["xdotool", "getwindowname", str(xid)],
                            capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xid", nargs="?", help="window id (decimal or 0x-hex)")
    ap.add_argument("--title", help="find the window by title substring instead of an id")
    args = ap.parse_args()

    if args.title:
        xid = find_by_title(args.title)
        if xid is None:
            print(f"clickthrough: no window found matching --title {args.title!r}",
                  file=sys.stderr)
            sys.exit(1)
    elif args.xid:
        try:
            xid = int(args.xid, 0)
        except ValueError:
            print(f"clickthrough: bad window id {args.xid!r}", file=sys.stderr)
            sys.exit(1)
    else:
        ap.print_help()
        sys.exit(1)

    ok, detail = clickthrough(xid)
    if not ok:
        print(f"clickthrough: FAILED on 0x{xid:x} — {detail}", file=sys.stderr)
        sys.exit(1)
    alive = still_renders(xid)
    print(f"clickthrough: OK — 0x{xid:x} input-shaped empty "
          f"(window {'confirmed still mapped' if alive else 'could NOT be confirmed — check by eye'})")


if __name__ == "__main__":
    main()
