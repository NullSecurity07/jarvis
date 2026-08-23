#!/usr/bin/env python3
# jarvis — tools/replay.py: replay a tape through the pinch gate, offline.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay `state/tape.json` (written by `bin/board.sh '{"a":"dump"}'`) through
the pinch gate's math, so a threshold can be judged against a RECORDED hand
instead of another live clinic session.

  tools/replay.py                       # summary of the last tape
  tools/replay.py state/tape.json --segments
  tools/replay.py --set pinch.worldTouchCm=3.5 --set pinch.gapFrontal=0.30
                                        # what-if: re-run with other numbers
  tools/replay.py --expect pinches=3 --expect claps=1   # regression (exit 1 on miss)

What it replays (from the raw landmarks the tape carries when tuning.json
has tape.raw = true): span, gap ratio, the three admitting signatures
(contrast / profile / curled), the 2D gap ceilings, THE WORLD LANE, the
charged EMA + two-frame instant path, and the speed-aware release — the
same formulas stage.html runs, with the tuning the tape was recorded under
(or --tuning FILE, or --set overrides). Probation and the claw/clap engines
are NOT replayed; the tape's own recorded flags (`p`, `clapFired`) are
reported beside the replay so you can see where they disagree.

This is a model of the gate, not the gate: treat a disagreement as a lead,
then confirm on the glass.
"""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "pinch": {"contrast": 0.18, "backFloor": 1.30, "profileAspect": 2.0,
              "profileT": 0.95, "curledT": 0.58, "curledAspect": 2.7,
              "gapFrontal": 0.32, "gapRotated": 0.38, "worldTouchCm": 2.5,
              "relSlow": 0.55, "relFast": 0.70, "fastSpd": 800,
              "emaBar": 0.55, "sanityAspect": 6},
    "fling": {"peak": 1300, "follow": 0.4},
    "tap": {"ms": 300, "px": 26},
}


def merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        if k.startswith("_"):
            continue
        out[k] = merge(a.get(k, {}), v) if isinstance(v, dict) else v
    return out


def setpath(d, path, val):
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    try:
        val = float(val)
    except ValueError:
        pass
    d[keys[-1]] = val


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def frame_metrics(lm):
    """The per-frame anatomy the pinch gate reads (2D, normalized coords)."""
    W0, M9 = lm[0], lm[9]
    span = dist(W0, M9)
    if span <= 0:
        return None
    gap = dist(lm[4], lm[8])
    ratio = gap / span

    def fR(t, m):
        td, md = dist(lm[t], W0), dist(lm[m], W0)
        return td / md if md > 0 else 9
    f8 = fR(8, 5)
    back = (fR(12, 9) + fR(16, 13) + fR(20, 17)) / 3
    palmW = dist(lm[5], lm[17])
    aspect = span / palmW if palmW > 0 else 9
    tRel = dist(lm[4], lm[13]) / span
    return {"ratio": ratio, "f8": f8, "back": back, "aspect": aspect,
            "tRel": tRel, "span": span}


def replay_hand(frames, T):
    """frames: list of (t_ms, x, y, lm, wcm, recorded_p). Returns per-frame
    replayed pinch flags + the lane that admitted each onset."""
    P = T["pinch"]
    pinched = False
    okEma = 0.0
    okPrev = False
    openPrev = False
    hist = []
    out = []
    for (t, x, y, lm, wcm, rec_p) in frames:
        m = frame_metrics(lm) if lm else None
        hist.append((x, y, t))
        if len(hist) > 10:
            hist.pop(0)
        hspd = 0.0
        if len(hist) >= 2:
            (x0, y0, t0), (x1, y1, t1) = hist[0], hist[-1]
            dt = (t1 - t0) / 1000
            if dt > 0:
                hspd = math.hypot(x1 - x0, y1 - y0) / dt
        if not m:
            out.append({"t": t, "p": pinched, "rec": rec_p, "lane": "", "m": None})
            continue
        ok = ((m["back"] - m["f8"] > P["contrast"] and m["back"] > P["backFloor"])
              or (m["aspect"] < P["profileAspect"] and m["tRel"] > P["profileT"])
              or (m["tRel"] > P["curledT"] and m["aspect"] > P["curledAspect"]))
        okEma = 0.70 * okEma + 0.30 * (1 if ok else 0)
        okNow = ok and okPrev
        okPrev = ok
        relBar = P["relFast"] if hspd > P["fastSpd"] else P["relSlow"]
        openRead = m["ratio"] >= relBar
        relOk = (openRead and openPrev) if hspd > P["fastSpd"] else openRead
        openPrev = openRead
        garbage = m["aspect"] > P["sanityAspect"]
        touch2d = m["ratio"] < (P["gapRotated"] if m["aspect"] < P["profileAspect"]
                                else P["gapFrontal"])
        touch3d = (P["worldTouchCm"] > 0 and wcm is not None
                   and wcm < P["worldTouchCm"])
        was = pinched
        if garbage:
            pinched = False
        elif was:
            pinched = not relOk
        else:
            pinched = (touch2d or touch3d) and (okNow or okEma > P["emaBar"])
        lane = ""
        if pinched and not was:
            lane = "2d" if touch2d else "world"
        out.append({"t": t, "p": pinched, "rec": rec_p, "lane": lane, "m": m,
                    "hspd": hspd, "wcm": wcm})
    return out


def segments(flags):
    """[(start_t, end_t)] for runs where flag is true."""
    segs, start = [], None
    for f in flags:
        if f["p"] and start is None:
            start = f["t"]
        if not f["p"] and start is not None:
            segs.append((start, f["t"]))
            start = None
    if start is not None:
        segs.append((start, flags[-1]["t"]))
    return segs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tape", nargs="?", default=str(HERE / "state" / "tape.json"))
    ap.add_argument("--tuning", help="tuning.json to replay under (default: "
                    "the tape's embedded tuning, else ../tuning.json)")
    ap.add_argument("--set", action="append", default=[],
                    metavar="KEY=VAL", help="override, e.g. pinch.worldTouchCm=3")
    ap.add_argument("--segments", action="store_true", help="list every pinch segment")
    ap.add_argument("--expect", action="append", default=[], metavar="WHAT=N",
                    help="assert pinches=N / claps=N / hands=N (exit 1 on miss)")
    a = ap.parse_args()

    tape = json.loads(Path(a.tape).read_text())
    rows = tape.get("rows") or []
    if not rows:
        print("empty tape")
        return 2
    T = merge(DEFAULTS, tape.get("tune") or {})
    if a.tuning:
        T = merge(T, json.loads(Path(a.tuning).read_text()))
    elif not tape.get("tune") and (HERE / "tuning.json").exists():
        T = merge(T, json.loads((HERE / "tuning.json").read_text()))
    for kv in a.set:
        k, _, v = kv.partition("=")
        setpath(T, k, v)

    # split the tape per hand id
    per = {}
    raw_frames = 0
    for r in rows:
        for h in r.get("h") or []:
            lm = h.get("lm")
            if lm:
                raw_frames += 1
            per.setdefault(h["id"], []).append(
                (r["t"], h.get("x", 0), h.get("y", 0), lm, h.get("w"), bool(h.get("p"))))
    t0, t1 = rows[0]["t"], rows[-1]["t"]
    dur = (t1 - t0) / 1000
    claps = sum(1 for r in rows if r.get("clapFired"))

    print(f"TAPE {a.tape}: {len(rows)} frames, {dur:.1f}s, fps {tape.get('fps', '?')}, "
          f"{len(per)} hand id(s), raw landmarks on {raw_frames} hand-frames, "
          f"claps fired (recorded): {claps}")
    if not raw_frames:
        print("  no raw landmarks on this tape — set tuning.json tape.raw = true, "
              "reload, and dump again; only the recorded flags can be reported.")

    total_replay = total_rec = 0
    lanes = {"2d": 0, "world": 0}
    disagree = 0
    for hid, frames in sorted(per.items()):
        out = replay_hand(frames, T)
        rep = segments(out)
        rec = segments([{"t": f["t"], "p": f["rec"]} for f in out])
        total_replay += len(rep)
        total_rec += len(rec)
        for f in out:
            if f["lane"]:
                lanes[f["lane"]] += 1
            if f["m"] and f["p"] != f["rec"]:
                disagree += 1
        ws = [f["wcm"] for f in out if f.get("wcm") is not None]
        wtxt = (f", world gap {min(ws):.1f}–{sorted(ws)[len(ws)//2]:.1f}–{max(ws):.1f} cm"
                if ws else "")
        print(f"  hand {hid}: {len(frames)} frames, recorded pinches {len(rec)}, "
              f"replayed pinches {len(rep)}{wtxt}")
        if a.segments:
            for s, e in rep:
                print(f"      replay  {(s - t0) / 1000:7.2f}s  {e - s:5d} ms")
            for s, e in rec:
                print(f"      recorded{(s - t0) / 1000:7.2f}s  {e - s:5d} ms")
    print(f"  onsets admitted by lane: 2d {lanes['2d']}, world-only {lanes['world']}; "
          f"frames where replay ≠ recorded: {disagree}")

    bad = 0
    for kv in a.expect:
        k, _, v = kv.partition("=")
        got = {"pinches": total_replay, "claps": claps, "hands": len(per)}.get(k)
        ok = got is not None and str(got) == v.strip()
        print(f"  expect {k}={v}: {'OK' if ok else 'MISS'} (got {got})")
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
