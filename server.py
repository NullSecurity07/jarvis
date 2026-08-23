#!/usr/bin/env python3
# jarvis: move things on your screen with your bare hands.
# Copyright (C) 2026 Jared Rhodenizer (original work, released as AGPL-3.0)
# Copyright (C) 2026 nullsec (the jarvis fork)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""jarvis server — serves the hand-tracked air-board on localhost.

localhost = a secure context, which is what lets the browser open your
camera for the tracker page. Nothing here ever leaves your machine.

Endpoints:
  GET  /stage.html, /media/*   the pages + the media airlock
  POST /state                  tracker's ~45Hz scene heartbeat; the response
                               carries queued commands (the command channel)
  GET  /state                  the render page mirrors the scene from here
  POST /cmd[?wait=ms]          board commands (your AI -> the board). Returns
                               {"id", "landed", "board"}; with wait=ms it
                               blocks until the tracker ACKS the command or
                               the wait runs out.
  GET  /config                 the jarvis.json config (name, orbs, res, fps)
  GET  /tuning                 tuning.json — every gesture/camera number the
                               stage reads at boot (hand-fitted, editable)
  GET  /board                  the persisted board (state/board.json): what
                               was on the glass at the last heartbeat; the
                               stage restores it after a reload
  GET  /owner                  the enrolled owner (state/owner.json) or {}
  POST /owner                  {vecs:[[128]..], at} enrolls; {"reset":true} forgets
  GET  /tree?orb=N             a notes orb's folder tree — read-only, JAILED
  GET  /note?f=N/<rel>         one note's text — read-only, JAILED
  GET  /props                  the media airlock as a browsable tree
  GET  /files?root=N&rel=<rel> a files-root directory listing — JAILED
  GET  /file?root=N&rel=<rel>  streams one file — JAILED, read-only,
                               range-request aware, capped at files.maxBytes
  GET  /desks                  live i3 workspaces + windows — desks.enabled
  GET  /desks/shot?xid=N       one PNG frame of a live window (maim)
  GET  /desks/stream?xid=N     that window as multipart/x-mixed-replace
  POST /desks/cmd              an i3-command allowlist (focus/move/etc), never kill
  GET  /orb                    your assistant's live state (the ring reads it)
  GET  /cam                    live V4L2 camera controls (Linux; the light
  POST /cam                    governor + camera clinic set them here)
  POST /dump                   the stage's tape (state/tape.json) and its
                               black box (state/errors.log)

Config lives in jarvis.json next to this file (re-read whenever it changes;
no restart needed):
  { "name": "JARVIS", "port": 8794, "res": "1280x720", "fps": "auto",
    "orbs": [ { "title": "Notes", "path": "sample-notes", "kind": "notes" },
              { "title": "Props", "path": "media",        "kind": "media" } ] }

"notes" orbs may point at ANY folder of markdown (an Obsidian vault is
just a folder of markdown). The "media" orb is always the repo's ./media
folder — the airlock: the only place images/models ever stage from.

"files" (optional) adds one browser orb per configured root, for anything
else — PDFs, code, archives, whatever is on disk:
  "files": { "roots": [ { "title": "Documents", "path": "~/Documents" } ],
             "maxBytes": 52428800 }
(absent key = a single "Documents" root at ~/Documents, 50MB cap; ~ itself
is deliberately never a shipped default — it would hand out ~/.ssh and
everything else in the home directory). Read-only, jailed to those roots
exactly like a notes orb (every dot-prefixed path segment — .ssh,
.bash_history, .config, .git, ... — is refused outright, wherever it
would land) — the user opts in by naming a root.

"desks" (optional, i3/X11 only) turns the board into a live view of your
own machine — every workspace and window becomes something you can put
on the glass:
  "desks": { "enabled": true, "fps": 4, "minGapMs": 180, "maxStreams": 3,
             "aliveMs": 1500, "display": ":0",
             "xauthority": "/run/user/1000/lyxauth" }
(absent key = disabled; every /desks* endpoint 404s). Read-only w.r.t.
your windows except an allowlist of i3 commands the page can send
(focus/workspace/move/layout/fullscreen — never kill); captures never
leave localhost, and the board's own window is always excluded.

Your AI drives the ring by writing tiny files into ./state/ :
  state/state      one word: idle | listening | thinking | speaking
  state/mood.json  {"mood": "green"|"amber"|"red", "ts": <unix time>}
  state/wave.json  {"samples": [0..1 x 64], "ts": <unix time>}
Missing files are fine — the ring just idles.
"""
import collections
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "jarvis.json"
TUNING_FILE = HERE / "tuning.json"
BOARD_FILE = HERE / "state" / "board.json"
OWNER_FILE = HERE / "state" / "owner.json"


# ---- THE CONFIG (hot): re-read whenever jarvis.json's mtime changes, so a
# new orb or a new name lands on the next request — no restart. The port
# is read once at start (a socket can't move under a running server).
_CFG_LOCK = threading.Lock()
_CFG = {"cfg": None, "mtime": None}


def _read_config():
    cfg = {"name": "JARVIS", "port": 8794, "orbs": [],
           "res": "1280x720", "fps": "auto"}
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except Exception:
        pass
    if not cfg.get("orbs"):
        cfg["orbs"] = [
            {"title": "Notes", "path": "sample-notes", "kind": "notes"},
            {"title": "Props", "path": "media", "kind": "media"},
        ]
    for orb in cfg["orbs"]:
        orb["path"] = str(Path(str(orb.get("path", ""))).expanduser())
    files = cfg.get("files") if isinstance(cfg.get("files"), dict) else {}
    if not files.get("roots"):
        # SECURITY: never ~ by default — that would hand out ~/.ssh and
        # everything else in the home directory to a page that boots
        # with no jarvis.json at all. Documents is opt-out, not opt-in.
        files["roots"] = [{"title": "Documents", "path": "~/Documents"}]
    if not files.get("maxBytes"):
        files["maxBytes"] = 52428800
    for r in files["roots"]:
        r["path"] = str(Path(str(r.get("path", ""))).expanduser())
    cfg["files"] = files
    desks = cfg.get("desks") if isinstance(cfg.get("desks"), dict) else {}
    desks.setdefault("enabled", False)
    desks.setdefault("fps", 4)
    desks.setdefault("minGapMs", 180)
    desks.setdefault("maxStreams", 3)
    # fix round 3: how often a /desks/stream loop re-confirms (via i3-msg)
    # that its window still exists, WHILE captures keep succeeding — a
    # failed capture always confirms immediately regardless of this gap
    # (see the stream loop). Bounds the steady-state i3-msg cost to
    # ~1000/aliveMs calls/sec per stream instead of one every frame.
    desks.setdefault("aliveMs", 1500)
    desks.setdefault("display", ":0")
    desks.setdefault("xauthority", "/run/user/1000/lyxauth")
    try:
        desks["fps"] = max(1, min(10, int(desks["fps"])))
    except Exception:
        desks["fps"] = 4
    try:
        desks["minGapMs"] = max(0, int(desks["minGapMs"]))
    except Exception:
        desks["minGapMs"] = 180
    try:
        desks["maxStreams"] = max(1, min(10, int(desks["maxStreams"])))
    except Exception:
        desks["maxStreams"] = 3
    try:
        desks["aliveMs"] = max(250, min(10000, int(desks["aliveMs"])))
    except Exception:
        desks["aliveMs"] = 1500
    cfg["desks"] = desks
    return cfg


def config():
    """The live config: reloaded when the file changed on disk."""
    try:
        mt = CONFIG_FILE.stat().st_mtime
    except OSError:
        mt = None
    with _CFG_LOCK:
        if _CFG["cfg"] is None or mt != _CFG["mtime"]:
            _CFG["cfg"] = _read_config()
            _CFG["mtime"] = mt
        return _CFG["cfg"]


def orb_root(i):
    """Resolve a notes orb's jail root, or None."""
    try:
        orb = config()["orbs"][int(i)]
        assert orb.get("kind") == "notes"
        p = Path(orb["path"])
        if not p.is_absolute():
            p = HERE / p
        return p.resolve()
    except Exception:
        return None


def files_root(i):
    """Resolve a files-browser root's jail root, or None."""
    try:
        r = config()["files"]["roots"][int(i)]
        p = Path(r["path"])
        if not p.is_absolute():
            p = HERE / p
        return p.resolve()
    except Exception:
        return None


def _jail_resolve(root, rel):
    """rel resolved against root -> the resolved Path, or None unless it
    lands AT or INSIDE root (symlink escapes rejected by comparing the
    RESOLVED paths, not the textual ones — shared by /note, /files, /file
    and the open_file command).

    SECURITY (fix round, 2026-08-24): every path SEGMENT of rel is
    rejected outright if it starts with "." — dotfiles/dotdirs (.ssh,
    .bash_history, .config, .git, ...) are never resolved, never listed,
    never served, full stop. This is checked on the raw segments before
    resolve() ever runs, which also rejects ".." traversal as a side
    effect (".." itself starts with a dot) — one check, three call sites
    (/files, /file, open_file) plus /note, all covered here. A SECOND
    check re-runs the same test on the RESOLVED path's segments (relative
    to root): a symlink can carry a dot-free name while pointing AT a
    dotfile (e.g. a plain-named symlink -> ~/.ssh/id_ed25519) — the first
    check alone would miss that laundering, since it only ever sees the
    name the caller typed, never where a symlink actually leads."""
    if root is None:
        return None
    rel = str(rel or "")
    for seg in rel.replace("\\", "/").split("/"):
        if seg.startswith("."):
            return None
    try:
        target = (root / rel).resolve()
    except Exception:
        return None
    if target != root and root not in target.parents:
        return None
    if target != root:
        for seg in target.relative_to(root).parts:
            if seg.startswith("."):
                return None
    return target


# ---- THE FILES BROWSER's kind classifier (extension -> a coarse type the
# page renders differently): explicit lists, not a library sniff — this
# never opens the file, so a renamed binary just lands in "other".
_KIND_EXT = {
    "md": {".md", ".markdown"},
    "text": {".txt", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
             ".xml", ".ini", ".cfg", ".conf", ".toml", ".env"},
    "code": {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".c",
              ".h", ".hpp", ".cpp", ".cc", ".rs", ".go", ".java", ".rb",
              ".sh", ".bash", ".zsh", ".fish", ".css", ".scss", ".html",
              ".htm", ".sql", ".php", ".swift", ".kt", ".lua", ".pl",
              ".r", ".m", ".vue", ".svelte"},
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg",
              ".avif"},
    "video": {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"},
    "audio": {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"},
    "model": {".glb", ".gltf"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z",
                ".rar"},
}
_EXT_KIND = {ext: k for k, exts in _KIND_EXT.items() for ext in exts}


def kind_of(ext):
    return _EXT_KIND.get(ext.lower(), "other")


def ctype_of(kind, name):
    if name.lower().endswith(".svg"):
        # an SVG can embed <script> that runs in the board's own origin
        # if a browser is ever pointed at /file by top-level navigation —
        # never served as image/svg+xml, whatever mimetypes would guess
        return "text/plain; charset=utf-8"
    if kind == "pdf":
        return "application/pdf"
    if kind in ("text", "code", "md"):
        return "text/plain; charset=utf-8"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# ---- THE DESKS: your own machine's windows on the glass (i3/X11 only).
# Read-only w.r.t. your windows except an allowlist of i3 commands sent as
# a LIST of argv (i3-msg joins them with a space itself — "i3-msg border
# normal" is documented as equivalent to "i3-msg 'border normal'") —
# never shell=True, never an interpolated string. kill is not in the
# allowlist: closing the user's windows by gesture isn't something to
# ship untested overnight.
_I3_MSG = "/usr/sbin/i3-msg"
_DESK_EXCLUDE_CLASS = {"polybar"}
# fix round 1: the original rule (title EXACTLY "jarvis") missed a real
# case live — the controller's verification browser was class
# Chromium-browser, title "jarvis – <tab title>", which slipped through
# and mirrored the board into infinity. Any BROWSER window (by class)
# whose title CONTAINS "jarvis" (case-insensitive) is the board now,
# covering any window-title suffix the tab adds. Firefox-family (firefox,
# zen — verified live during round 2 testing: a real "...jarvis... — Zen
# Browser" window existed on this machine and was NOT excluded before
# this line) added alongside Chromium-family for the same reason; the
# launcher only opens the board in a dedicated Chrome profile, but the
# self-mirror risk this guards against applies to whatever browser a
# jarvis-titled tab happens to be open in.
_DESK_BROWSER_CLASS = {"google-chrome", "chromium", "chromium-browser", "chrome",
                       "firefox", "zen"}
_DESK_LOCK = threading.Lock()
_DESK_CACHE = {}          # xid -> {"t": monotonic, "png": bytes}
_DESK_STREAMS = 0         # concurrent /desks/stream connections


def _desks_int(v):
    """int(v), but never through bool (True/False ARE ints in Python) —
    the same numeric-field discipline owner_validate uses above."""
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except Exception:
        return None


def _desks_env():
    d = config().get("desks", {})
    env = dict(os.environ)
    env["DISPLAY"] = str(d.get("display", ":0"))
    env["XAUTHORITY"] = str(d.get("xauthority", "/run/user/1000/lyxauth"))
    return env


def _i3(*args):
    """i3-msg <args...> (argv list, never shell) -> parsed JSON, or None."""
    try:
        r = subprocess.run([_I3_MSG, *args], capture_output=True, text=True,
                           timeout=3, env=_desks_env())
        return json.loads(r.stdout) if r.stdout else None
    except Exception:
        return None


def _desks_snapshot():
    """{"ws":[{"num","name","visible","focused"}],
    "wins":[{"cid","xid","ws","cls","title","w","h","focused","visible"}]}
    from get_workspaces + get_tree. The board's own window (any browser
    window — see _DESK_BROWSER_CLASS — whose title contains "jarvis",
    case-insensitive) and Polybar are excluded so the glass never mirrors
    itself into infinity. Never raises — an i3-msg failure just yields an
    empty board."""
    ws_raw = _i3("-t", "get_workspaces") or []
    tree = _i3("-t", "get_tree")
    ws = [{"num": w.get("num"), "name": w.get("name"),
           "visible": bool(w.get("visible")), "focused": bool(w.get("focused"))}
          for w in ws_raw if isinstance(w, dict)]
    vis_by_num = {w["num"]: w["visible"] for w in ws}
    wins = []

    def walk(n, cur_ws):
        ws_num = n.get("num") if n.get("type") == "workspace" else cur_ws
        win = n.get("window")
        if win:
            props = n.get("window_properties") or {}
            cls = props.get("class") or ""
            title = n.get("name") or ""
            cls_l = cls.lower()
            is_board = cls_l in _DESK_BROWSER_CLASS and "jarvis" in title.lower()
            if not is_board and cls_l not in _DESK_EXCLUDE_CLASS:
                rect = n.get("rect") or {}
                wins.append({"cid": n.get("id"), "xid": win, "ws": ws_num,
                            "cls": cls, "title": title,
                            "w": rect.get("width"), "h": rect.get("height"),
                            "focused": bool(n.get("focused")),
                            "visible": bool(vis_by_num.get(ws_num))})
        for c in n.get("nodes", []) + n.get("floating_nodes", []):
            walk(c, ws_num)
    if isinstance(tree, dict):
        walk(tree, None)
    # fix round 2: _DESK_CACHE never shrank on its own — every xid ever
    # captured over the process's lifetime kept its last PNG in memory
    # permanently, even long after the window closed. Every call here
    # (every /desks*, and every /desks/stream loop tick) opportunistically
    # drops entries for xids no longer in the live tree.
    with _DESK_LOCK:
        alive = {w["xid"] for w in wins}
        for stale in [xid for xid in _DESK_CACHE if xid not in alive]:
            del _DESK_CACHE[stale]
    return {"ws": ws, "wins": wins}


def _desks_valid_ws_ref(ref, snap):
    if isinstance(ref, bool):
        return False
    if isinstance(ref, int):
        return any(w["num"] == ref for w in snap["ws"])
    if isinstance(ref, str) and ref:
        return any(w["name"] == ref for w in snap["ws"])
    return False


def _desks_capture(xid):
    """maim -i <xid> -f png /dev/stdout -> (png_bytes_or_None, ok). Cached
    per-xid for desks.minGapMs so a burst of shot/stream hits on the same
    window doesn't restart the ~70ms capture on every request. ok=False
    means the FRESH capture failed — png is the last good frame if one
    was ever captured, else None (the caller turns that into a 503)."""
    gap = int(config().get("desks", {}).get("minGapMs", 180)) / 1000
    now = time.monotonic()
    with _DESK_LOCK:
        c = _DESK_CACHE.get(xid)
        if c and now - c["t"] < gap:
            return c["png"], True
    png = None
    try:
        r = subprocess.run(["maim", "-i", str(xid), "-f", "png", "/dev/stdout"],
                           capture_output=True, timeout=3, env=_desks_env())
        if r.returncode == 0 and r.stdout[:8] == b"\x89PNG\r\n\x1a\n":
            png = r.stdout
    except Exception:
        png = None
    with _DESK_LOCK:
        if png:
            _DESK_CACHE[xid] = {"t": now, "png": png}
            return png, True
        c = _DESK_CACHE.get(xid)
        return (c["png"] if c else None), False


def cam_dev():
    return str(config().get("camera_dev", "/dev/video0"))


# ---- THE SCENE + THE COMMAND CHANNEL.
# The tracker POSTs its scene ~45x/s; the render page and your AI's eyes
# GET it. Commands queue here with an id; the tracker drains them in its
# heartbeat response and reports the highest id it applied (`ack`) in its
# next heartbeat — that is what lets POST /cmd?wait= tell you "landed"
# instead of "queued" (the board can be hidden/throttled, in which case a
# command sits here unseen; you deserve to know).
_LOCK = threading.Lock()
_STATE = b"{}"          # latest scene state: tracker POSTs, render GETs
_STATE_T = 0.0          # when the last heartbeat arrived (monotonic)
_CMDS = collections.deque()   # queued board commands, each carries an id
_CMD_SEQ = 0
_ACK = 0                # highest command id the tracker reports applied
_ACK_EV = threading.Condition(_LOCK)
_ALLOWED = ("add_img", "add_card", "clear", "reset", "hand", "give",
            "yank", "hover", "scroll_note", "widget", "explode", "assemble",
            "present", "clinic", "light", "reload", "dump",
            "enroll", "guest", "lock",          # THE OWNER LOCK
            "open_file",                        # THE FILES BROWSER
            "desk")                             # THE DESKS
_MAX_QUEUE = 256        # a hidden board must not grow a silent backlog forever

# ---- THE BOARD FILE: what is on the glass, persisted so a reload (or a
# server restart) brings it back. Written from the heartbeat, at most
# once a second, and only when the scene actually changed.
_BOARD = {"sig": None, "t": 0.0}
_BOARD_TYPES = ("card", "img", "model", "panel", "media")
# ptype/fsrc/proot/prel/mk (Task 16 fix round 1): a files-browser panel or
# media card round-trips through /file on restore, not the notes-jailed
# /note — the page (pg/pgs) is deliberately NOT persisted, a restored PDF
# just reopens at page 1.
_BOARD_KEYS = ("type", "title", "body", "src", "mm", "lf", "ex", "rx", "ry",
               "rz", "file", "x", "y", "scale", "ptype", "fsrc", "proot",
               "prel", "mk")


def _persist_board(body):
    now = time.monotonic()
    if now - _BOARD["t"] < 1.0:
        return
    try:
        items = json.loads(body).get("items") or []
    except Exception:
        return
    keep = [{k: i.get(k) for k in _BOARD_KEYS if i.get(k) is not None}
            for i in items if i.get("type") in _BOARD_TYPES]
    sig = json.dumps(keep, sort_keys=True)
    if sig == _BOARD["sig"]:
        return
    _BOARD["sig"] = sig
    _BOARD["t"] = now
    try:
        BOARD_FILE.parent.mkdir(exist_ok=True)
        BOARD_FILE.write_text(json.dumps({"at": time.time(), "items": keep}))
    except Exception:
        pass


def _finite_row(v, n):
    """A list of exactly n finite (non-bool) numbers -> rounded floats, or None."""
    if not isinstance(v, list) or len(v) != n:
        return None
    row = []
    for x in v:
        if isinstance(x, bool):
            return None
        try:
            row.append(float(x))
        except (TypeError, ValueError):
            return None
    if any(x != x or x in (float("inf"), float("-inf")) for x in row):
        return None
    return [round(x, 6) for x in row]


def owner_validate(obj):
    """{vecs: 1..16 x 128 finite numbers, at,
    geom?: 6 finite numbers (Task 11: the enrolled geometry ratios),
    prop?: 1 finite number (Task 11: the enrolled body-proportion tie-breaker),
    seeds?: int 0..16 (Task 11: how many vecs are enrolment seeds, never
    dropped by drift)} -> cleaned dict, or None. driftCap stays <= 16, the
    cap this already enforced — nothing to raise."""
    try:
        vecs = obj.get("vecs")
        if not isinstance(vecs, list) or not 1 <= len(vecs) <= 16:
            return None
        clean = []
        for v in vecs:
            row = _finite_row(v, 128)
            if row is None:
                return None
            clean.append(row)
        out = {"vecs": clean, "at": float(obj.get("at") or time.time())}
        if obj.get("geom") is not None:
            g = _finite_row(obj["geom"], 6)
            if g is None:
                return None
            out["geom"] = g
        if obj.get("prop") is not None:
            p = _finite_row([obj["prop"]], 1)
            if p is None:
                return None
            out["prop"] = p[0]
        if obj.get("seeds") is not None:
            s = obj["seeds"]
            if isinstance(s, bool) or not isinstance(s, int) or not 0 <= s <= 16:
                return None
            out["seeds"] = s
        return out
    except Exception:
        return None


# ---- THE CAMERA BRIDGE (/cam). Chrome can't reach every V4L2 control
# (exposure_dynamic_framerate, the ROI metering pair), and those are the
# ones that decide whether a backlit room runs at 17fps or 30. The stage
# sets them through here, live, while Chrome holds the device (uvcvideo
# allows a second fd for controls). Allowlisted names + ranges only,
# localhost only like everything else; no v4l2-ctl -> 501 and the page
# simply runs telemetry-only.
_CAM_CTRLS = {                      # name: (lo, hi) or None for the rect
    "auto_exposure": (1, 3), "exposure_time_absolute": (4, 1250),
    "exposure_dynamic_framerate": (0, 1), "backlight_compensation": (0, 2),
    "brightness": (0, 255), "contrast": (0, 255), "gamma": (90, 150),
    "sharpness": (0, 7), "saturation": (0, 100),
    "region_of_interest_auto_ctrls": (0, 255),
    "region_of_interest_rectangle": None,
}
_RECT_RE = re.compile(r"^\(\d{1,4},\d{1,4}\)/\d{1,4}x\d{1,4}$")

# THE DNS-REBINDING GUARD: "listens on 127.0.0.1" alone is not actually
# localhost-only — a page on an attacker's DNS name that resolves to
# 127.0.0.1 still gets a same-origin fetch to this port, and the browser
# sends THAT name in Host. Every request must carry a Host the browser
# could only send by actually talking to us at loopback.
_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.IGNORECASE)


def cam_read():
    """Readback of every allowlisted control: {name: value-string}."""
    if not shutil.which("v4l2-ctl"):
        return None
    r = subprocess.run(["v4l2-ctl", "-d", cam_dev(), "-C",
                        ",".join(_CAM_CTRLS)], capture_output=True,
                       text=True, timeout=3)
    out = {}
    for ln in r.stdout.splitlines():
        k, _, v = ln.partition(":")
        if _:
            out[k.strip()] = v.strip().split(" ")[0]
    return out


def cam_set(pairs):
    """Apply {name: value} (validated); returns the per-control errors."""
    errs = {}
    for k, v in pairs.items():
        rng = _CAM_CTRLS.get(k, "NA")
        if rng == "NA":
            errs[k] = "not allowed"
            continue
        if rng is None:
            v = str(v)
            if not _RECT_RE.match(v):
                errs[k] = "bad rect"
                continue
        else:
            try:
                v = int(v)
                assert rng[0] <= v <= rng[1]
            except Exception:
                errs[k] = "out of range"
                continue
        r = subprocess.run(["v4l2-ctl", "-d", cam_dev(), "-c", f"{k}={v}"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode or r.stderr.strip():
            errs[k] = (r.stderr.strip() or f"rc {r.returncode}")[:120]
    return errs


def board_status():
    """live | stale | down — how fresh the tracker's heartbeat is."""
    age = time.monotonic() - _STATE_T if _STATE_T else None
    if age is None or age > 10:
        return "down", age
    return ("live" if age < 1.5 else "stale"), age


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # no-store on the pages and the /js/ modules they import, so a
        # plain reload always serves current code (Chrome happily caches
        # through reloads otherwise) — a stale cached module next to a
        # fresh page breaks the import graph at instantiation (a page can
        # die before any code, even the black box, runs). Media/model
        # files stay cacheable.
        p = self.path.split("?")[0]
        if p.endswith(".html") or p.startswith("/js/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def log_message(self, *a):
        pass

    def _host_ok(self):
        return bool(_HOST_RE.match(self.headers.get("Host", "")))

    def do_HEAD(self):
        # otherwise only reaches the inherited static-file HEAD (serves
        # from HERE, the repo directory) — the same Host guard as GET/POST,
        # for the same reason: it must cover every verb, not just two
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        url = urllib.parse.urlparse(self.path)
        if url.path == "/file":
            # pdf.js and some video/audio clients probe with HEAD (for
            # Content-Length/Accept-Ranges) before ever sending a Range
            # GET — same jailed stream as GET, just without the body
            self._serve_file(urllib.parse.parse_qs(url.query), config(),
                              want_body=False)
            return
        super().do_HEAD()

    def handle(self):
        # the tracker drops heartbeat connections mid-reply all the time
        # (a reload, a throttled tab): that is not an error worth a
        # traceback in the launcher log
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, body, code=200, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, q, cfg, want_body=True):
        # streams one file — JAILED like /files, read-only, no exec. Real
        # range support (206 + Content-Range): pdf.js and <video>/<audio>
        # all issue Range requests, and without a real 206 they either
        # refuse to play or refetch the whole file per seek. Shared by
        # GET (want_body=True) and HEAD (want_body=False, same headers,
        # no bytes — some players probe with HEAD before ever ranging).
        idx = (q.get("root") or ["0"])[0]
        rel = (q.get("rel") or [""])[0]
        root = files_root(idx)
        target = _jail_resolve(root, rel)
        if target is None or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        try:
            size = target.stat().st_size
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        max_bytes = int(cfg.get("files", {}).get("maxBytes", 52428800))
        ctype = ctype_of(kind_of(target.suffix), target.name)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                if size == 0 or start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206
        elif size > max_bytes:
            # no Range: the whole file would have to be served, and the
            # whole file is over the cap — refuse outright
            self.send_response(413)
            self.end_headers()
            return
        length = end - start + 1
        if length > max_bytes:
            # the cap governs BYTES SERVED, not file size: a ranged
            # request under the cap streams fine even off an oversized
            # file (this is what actually lets a big video/audio file
            # play at all — the whole-file check above only catches an
            # unranged request)
            self.send_response(413)
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not want_body:
            return
        try:
            with open(target, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        global _STATE, _STATE_T, _CMD_SEQ, _ACK
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        url = urllib.parse.urlparse(self.path)
        path, q = url.path, urllib.parse.parse_qs(url.query)
        n = int(self.headers.get("Content-Length", 0) or 0)
        cap = 64_000_000 if path == "/dump" else 262144
        body = self.rfile.read(n) if 0 < n < cap else b"{}"
        if path == "/state":
            # the tracker's heartbeat doubles as the command channel:
            # it carries `ack` (highest command id applied) in, and
            # takes the next batch of commands out — under one lock, so
            # a command can never be dropped between "read" and "delete"
            batch = []
            with _LOCK:
                _STATE = body
                _STATE_T = time.monotonic()
                m = re.search(rb'"ack":\s*(\d+)', body[:4096])
                if m:
                    _ACK = max(_ACK, int(m.group(1)))
                    _ACK_EV.notify_all()
                while _CMDS and len(batch) < 8:
                    batch.append(_CMDS.popleft())
            self._raw(json.dumps(batch).encode())
            _persist_board(body)
            return
        if path == "/dump":
            # the stage's tape (cmd dump) lands in state/tape.json for
            # whoever is tuning — your AI reads it, fits, and edits
            try:
                (HERE / "state").mkdir(exist_ok=True)
                if b'"err"' in body[:64]:       # the tracker's black box
                    with open(HERE / "state" / "errors.log", "ab") as f:
                        f.write(time.strftime("%H:%M:%S ").encode() + body + b"\n")
                else:
                    (HERE / "state" / "tape.json").write_bytes(body)
                self.send_response(204)
            except Exception:
                self.send_response(500)
            self.end_headers()
            return
        if path == "/cam":
            # live camera controls from the stage (the light governor,
            # the camera clinic) or from you: {"set": {name: value}}
            try:
                req = json.loads(body or b"{}")
                pairs = req.get("set") or {}
                assert isinstance(pairs, dict) and len(pairs) <= 16
            except Exception:
                self._json({"err": "bad request"}, 400)
                return
            if not shutil.which("v4l2-ctl"):
                self._json({"err": "no v4l2"}, 501)
                return
            try:
                errs = cam_set(pairs)
                self._json({"ok": not errs, "errs": errs,
                            "cam": cam_read()}, 200 if not errs else 400)
            except Exception as e:
                self._json({"err": str(e)[:200]}, 500)
            return
        if path == "/owner":
            # THE OWNER LOCK's one file: the enrolled owner's descriptors.
            # {"reset": true} deletes it (cmd enroll); otherwise validated
            # and written whole. Nothing else in the system writes here.
            if n > 65536:
                self._json({"err": "owner: need 1-16 vectors of 128 numbers"}, 400)
                return
            try:
                req = json.loads(body)
            except Exception:
                self._json({"err": "bad json"}, 400)
                return
            if not isinstance(req, dict):
                self._json({"err": "bad json"}, 400)
                return
            if req.get("reset"):
                try:
                    OWNER_FILE.unlink()
                except FileNotFoundError:
                    pass
                self._json({"ok": True, "reset": True})
                return
            clean = owner_validate(req)
            if clean is None:
                self._json({"err": "owner: need 1-16 vectors of 128 numbers"}, 400)
                return
            OWNER_FILE.parent.mkdir(exist_ok=True)
            OWNER_FILE.write_text(json.dumps(clean))
            self._json({"ok": True, "n": len(clean["vecs"])})
            return
        if path == "/cmd":
            try:
                cmd = json.loads(body)
                assert cmd.get("a") in _ALLOWED
                if cmd["a"] in ("add_img", "hand", "give", "present") and cmd.get("src"):
                    # THE AIRLOCK: only files really inside ./media/ ever
                    # stage — subfolders allowed, escapes 400. If the
                    # exact path misses, a UNIQUE basename match anywhere
                    # inside the airlock self-heals a wrong-folder guess;
                    # zero or many matches still 400.
                    rel = str(cmd.get("src", "")).lstrip("/")
                    if rel.startswith("media/"):
                        rel = rel[6:]
                    media = (HERE / "media").resolve()
                    target = (media / rel).resolve()
                    if media not in target.parents or not target.is_file():
                        name = Path(rel).name.lower()
                        hits = [p for p in media.rglob("*")
                                if p.is_file()
                                and p.name.lower() == name] if name else []
                        if len(hits) != 1:
                            raise ValueError("not in the media airlock")
                        target = hits[0]
                    cmd["src"] = "/media/" + target.relative_to(media).as_posix()
                elif cmd["a"] == "open_file":
                    # THE FILES BROWSER, staged by the assistant: jailed
                    # exactly like a user tap — resolve, then require the
                    # configured root to be an ancestor (or the file itself)
                    root = files_root(cmd.get("root"))
                    target = _jail_resolve(root, cmd.get("rel"))
                    if target is None or not target.is_file():
                        raise ValueError("not in a configured files root")
                    cmd["root"] = int(cmd["root"])
                    cmd["rel"] = target.relative_to(root).as_posix()
                elif cmd["a"] == "desk":
                    # THE DESKS, staged by the assistant: same jail as a
                    # user tap — every xid/ws must exist in the CURRENT
                    # i3 tree, checked fresh (not cached) right here.
                    if not config().get("desks", {}).get("enabled"):
                        raise ValueError("desks disabled")
                    snap = _desks_snapshot()
                    xid = _desks_int(cmd.get("xid")) if "xid" in cmd else None
                    ws = cmd.get("ws") if "ws" in cmd else None
                    if xid is not None:
                        if not any(w["xid"] == xid for w in snap["wins"]):
                            raise ValueError("unknown xid")
                        cmd["xid"] = xid
                    elif ws is not None:
                        if not _desks_valid_ws_ref(ws, snap):
                            raise ValueError("unknown workspace")
                        cmd["ws"] = ws
                    else:
                        raise ValueError("desk needs xid or ws")
            except Exception:
                self._json({"err": "rejected: unknown action or file "
                                   "outside the media airlock"}, 400)
                return
            wait_ms = 0
            try:
                wait_ms = max(0, min(30000, int((q.get("wait") or ["0"])[0])))
            except ValueError:
                pass
            with _LOCK:
                _CMD_SEQ += 1
                cid = _CMD_SEQ
                cmd["id"] = cid
                _CMDS.append(cmd)
                while len(_CMDS) > _MAX_QUEUE:
                    _CMDS.popleft()
                landed = False
                if wait_ms:
                    deadline = time.monotonic() + wait_ms / 1000
                    while _ACK < cid:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            break
                        _ACK_EV.wait(left)
                    landed = _ACK >= cid
            status, age = board_status()
            self._json({"id": cid, "landed": landed, "board": status,
                        "age": None if age is None else round(age, 1)})
            return
        if path == "/desks/cmd":
            # THE DESKS' control channel: an i3-command ALLOWLIST, never
            # free-form text — kill is not here (v1 never closes the
            # user's windows by gesture). Every numeric field is
            # validated as an int and every cid/ws must exist in the
            # CURRENT i3 tree, fetched fresh for this request.
            if not config().get("desks", {}).get("enabled"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                cmd = json.loads(body)
                a = cmd.get("a")
                # fix round 2: assert is stripped under python -O — this
                # must not be the stated line of defence for the action
                # allowlist (the if/elif chain below re-checks the same
                # strings, which is why it was harmless in practice, but
                # that's incidental, not the design).
                if a not in ("workspace", "focus", "move", "layout", "fullscreen"):
                    raise ValueError("unknown action")
                snap = _desks_snapshot()
                if a == "workspace":
                    n = cmd.get("n")
                    if not _desks_valid_ws_ref(n, snap):
                        raise ValueError("unknown workspace")
                    _i3("workspace", "number", str(n)) if isinstance(n, int) \
                        else _i3("workspace", str(n))
                elif a == "focus":
                    cid = _desks_int(cmd.get("cid"))
                    if cid is None or not any(w["cid"] == cid for w in snap["wins"]):
                        raise ValueError("unknown cid")
                    _i3(f"[con_id={cid}]", "focus")
                elif a == "move":
                    cid = _desks_int(cmd.get("cid"))
                    ws = cmd.get("ws")
                    if cid is None or not any(w["cid"] == cid for w in snap["wins"]):
                        raise ValueError("unknown cid")
                    if not _desks_valid_ws_ref(ws, snap):
                        raise ValueError("unknown workspace")
                    dest = "number " + str(ws) if isinstance(ws, int) else str(ws)
                    _i3(f"[con_id={cid}]", "move", "container", "to",
                       "workspace", dest)
                elif a == "layout":
                    mode = cmd.get("mode")
                    if mode not in ("split", "tabbed", "stacking", "toggle split"):
                        raise ValueError("bad mode")
                    _i3("layout", *mode.split(" "))
                elif a == "fullscreen":
                    cid = _desks_int(cmd.get("cid"))
                    if cid is None or not any(w["cid"] == cid for w in snap["wins"]):
                        raise ValueError("unknown cid")
                    _i3(f"[con_id={cid}]", "fullscreen", "toggle")
            except Exception:
                self._json({"err": "rejected: unknown action, bad field, "
                                   "or not in the current i3 tree"}, 400)
                return
            self._json({"ok": True})
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if not self._host_ok():
            self.send_response(403)
            self.end_headers()
            return
        url = urllib.parse.urlparse(self.path)
        path, q = url.path, urllib.parse.parse_qs(url.query)
        cfg = config()
        if path == "/config":
            # the page builds its ring name + orb bloom from this, and
            # takes its camera defaults (res / fps) unless the URL says.
            # Files roots append AFTER the notes/media orbs (their array
            # position is untouched — /tree?orb=N and /note?f=N/... still
            # index by that position) each carrying its OWN "root" index
            # into cfg["files"]["roots"] for /files and /file.
            orbs = [{"title": o.get("title", "?"),
                     "kind": o.get("kind", "notes")} for o in cfg["orbs"]]
            for i, r in enumerate(cfg.get("files", {}).get("roots", [])):
                orbs.append({"title": r.get("title", "Files"),
                             "kind": "files", "root": i})
            if cfg.get("desks", {}).get("enabled"):
                orbs.append({"title": "Desks", "kind": "desks"})
            self._json({"name": cfg.get("name", "JARVIS"),
                        "res": cfg.get("res"), "fps": cfg.get("fps"),
                        "orbs": orbs,
                        "desksMax": cfg.get("desks", {}).get("maxStreams", 3)})
            return
        if path == "/tuning":
            # every hand-fitted number the stage reads at boot. Missing
            # file = {} = the stage's built-in defaults.
            try:
                self._raw(TUNING_FILE.read_bytes())
            except OSError:
                self._json({})
            except Exception:
                self._json({"err": "tuning.json unreadable"}, 500)
            return
        if path == "/board":
            try:
                self._raw(BOARD_FILE.read_bytes())
            except OSError:
                self._json({"items": []})
            return
        if path == "/owner":
            try:
                self._raw(OWNER_FILE.read_bytes())
            except OSError:
                self._json({})
            return
        if path == "/tree":
            # a notes orb's folder tree. Jailed to that orb's configured
            # folder, .md only, CLAUDE.md (AI config, not a note) excluded.
            idx = (q.get("orb") or ["0"])[0]
            root = orb_root(idx)
            if root is None or not root.is_dir():
                self._json({"name": "?", "notes": [], "dirs": []}, 404)
                return

            def walk(d):
                out = {"name": d.name, "notes": [], "dirs": []}
                for p in sorted(d.iterdir()):
                    if p.name.startswith("."):
                        continue
                    if p.is_dir():
                        sub = walk(p)
                        if sub["notes"] or sub["dirs"]:
                            out["dirs"].append(sub)
                    elif p.suffix == ".md" and p.name != "CLAUDE.md":
                        # note files travel as "<orb>/<relpath>" so /note
                        # knows which jail to resolve them against
                        out["notes"].append(
                            {"title": p.stem,
                             "file": f"{int(idx)}/{p.relative_to(root)}"})
                return out
            try:
                tree = walk(root)
                tree["name"] = cfg["orbs"][int(idx)].get("title", tree["name"])
                self._json(tree)
            except Exception:
                self._json({"name": "?", "notes": [], "dirs": []}, 500)
            return
        if path == "/props":
            # the media airlock as a browsable tree — live filesystem
            # read: drop a file in media/, reopen the orb, it's there
            EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm",
                    ".glb", ".gltf"}
            media_root = (HERE / "media").resolve()

            def walkm(d):
                out = {"name": d.name, "items": [], "dirs": []}
                for p in sorted(d.iterdir()):
                    if p.name.startswith("."):
                        continue
                    if p.is_dir():
                        sub = walkm(p)
                        if sub["items"] or sub["dirs"]:
                            out["dirs"].append(sub)
                    elif p.suffix.lower() in EXTS:
                        out["items"].append(str(p.relative_to(media_root)))
                return out
            try:
                tree = walkm(media_root)
                tree["name"] = "Props"
                self._json(tree)
            except Exception:
                self._json({"name": "Props", "items": [], "dirs": []}, 500)
            return
        if path == "/files":
            # a files-root directory listing — one level, lazily paged by
            # the page as it descends (unlike /tree, which walks a whole
            # notes vault upfront: an arbitrary root can be huge). JAILED
            # exactly like /note: resolve, then require the root to be an
            # ancestor (or the dir itself) of the resolved target.
            idx = (q.get("root") or ["0"])[0]
            rel = (q.get("rel") or [""])[0]
            root = files_root(idx)
            target = _jail_resolve(root, rel)
            if target is None or not target.is_dir():
                self._json({"err": "not found"}, 404)
                return
            dirs, files = [], []
            try:
                for p in target.iterdir():
                    if p.name.startswith("."):
                        continue
                    relp = p.relative_to(root).as_posix()
                    if p.is_dir():
                        dirs.append({"name": p.name, "rel": relp})
                    elif p.is_file():
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        files.append({"name": p.name, "rel": relp,
                                      "ext": p.suffix.lower(),
                                      "size": st.st_size,
                                      "mtime": int(st.st_mtime),
                                      "kind": kind_of(p.suffix)})
            except OSError:
                self._json({"err": "unreadable"}, 500)
                return
            dirs.sort(key=lambda d: d["name"].lower())
            files.sort(key=lambda f: f["name"].lower())
            rel_norm = "" if target == root else target.relative_to(root).as_posix()
            up = None
            if target != root:
                parent = target.parent
                up = "" if parent == root else parent.relative_to(root).as_posix()
            title = None
            try:
                title = config()["files"]["roots"][int(idx)].get("title")
            except Exception:
                pass
            self._json({"root": int(idx), "title": title, "rel": rel_norm,
                        "up": up, "dirs": dirs, "files": files})
            return
        if path == "/file":
            self._serve_file(q, cfg)
            return
        if path in ("/desks", "/desks/shot", "/desks/stream") \
                and not cfg.get("desks", {}).get("enabled"):
            # the feature can be turned off entirely — every /desks*
            # endpoint 404s, same as if it didn't exist
            self.send_response(404)
            self.end_headers()
            return
        if path == "/desks":
            self._json(_desks_snapshot())
            return
        if path == "/desks/shot":
            try:
                xid = int((q.get("xid") or [""])[0])
            except ValueError:
                self._json({"err": "bad xid"}, 400)
                return
            snap = _desks_snapshot()
            if not any(w["xid"] == xid for w in snap["wins"]):
                self._json({"err": "unknown window"}, 400)
                return
            png, ok = _desks_capture(xid)
            if png is None:
                self._json({"err": "capture failed"}, 503)
                return
            self._raw(png, 200 if ok else 503, "image/png")
            return
        if path == "/desks/stream":
            global _DESK_STREAMS
            try:
                xid = int((q.get("xid") or [""])[0])
            except ValueError:
                self._json({"err": "bad xid"}, 400)
                return
            snap = _desks_snapshot()
            if not any(w["xid"] == xid for w in snap["wins"]):
                self._json({"err": "unknown window"}, 400)
                return
            with _DESK_LOCK:
                if _DESK_STREAMS >= int(cfg.get("desks", {}).get("maxStreams", 3)):
                    self._json({"err": "too many desk streams"}, 429)
                    return
                _DESK_STREAMS += 1
            try:
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=jarvisframe")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                d = cfg.get("desks", {})
                period = 1.0 / max(1, min(10, int(d.get("fps", 4))))
                alive_gap = int(d.get("aliveMs", 1500)) / 1000
                last_check = time.monotonic()   # already confirmed once, above
                while True:
                    png, ok = _desks_capture(xid)
                    now = time.monotonic()
                    # fix round 2: a closed window's stream used to just
                    # keep re-sending its last cached frame (or nothing)
                    # forever, silently occupying one of only maxStreams
                    # slots. fix round 3: checking existence EVERY tick
                    # (get_workspaces+get_tree over the i3 IPC socket) was
                    # an always-on cost scaling with fps*maxStreams on a
                    # board meant to run all day — so this now confirms on
                    # two triggers instead of every frame: immediately
                    # when a capture just FAILED (strong evidence the
                    # window is gone — costs nothing while healthy), and
                    # otherwise at most once per desks.aliveMs as a
                    # backstop (covers a stale minGapMs cache hit still
                    # reporting ok=True after the window's actually gone).
                    if not ok or now - last_check >= alive_gap:
                        last_check = now
                        if not any(w["xid"] == xid for w in _desks_snapshot()["wins"]):
                            break
                    if png:
                        self.wfile.write(
                            b"--jarvisframe\r\nContent-Type: image/png\r\n"
                            b"Content-Length: " + str(len(png)).encode() +
                            b"\r\n\r\n" + png + b"\r\n")
                        self.wfile.flush()
                    time.sleep(period)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                with _DESK_LOCK:
                    _DESK_STREAMS -= 1
            return
        if path == "/orb":
            # the ring's heartbeat: your assistant's live state, read from
            # tiny files in ./state/. Every read fails soft — no files,
            # no assistant, no problem: the ring just breathes.
            s_dir = HERE / "state"
            out = {"state": "idle", "mood": "green", "wave": None}
            try:
                s = (s_dir / "state").read_text().strip().lower()
                if s in ("idle", "listening", "thinking", "speaking"):
                    out["state"] = s
            except Exception:
                pass
            try:
                m = json.loads((s_dir / "mood.json").read_text())
                if time.time() - float(m.get("ts", 0)) < 45.0:
                    out["mood"] = m.get("mood", "green")
            except Exception:
                pass
            if out["state"] == "speaking":
                try:
                    w = json.loads((s_dir / "wave.json").read_text())
                    if time.time() - float(w.get("ts", 0)) < 0.6:
                        out["wave"] = w.get("samples", [])[:64]
                except Exception:
                    pass
            self._json(out)
            return
        if path == "/cam":
            # the camera's live controls (the governor's readback channel)
            try:
                c = cam_read()
                if c is None:
                    self._json({"err": "no v4l2"}, 501)
                else:
                    self._json({"dev": cam_dev(), "cam": c})
            except Exception as e:
                self._json({"err": str(e)[:200]}, 500)
            return
        if path == "/state":
            with _LOCK:
                body = _STATE
            self._raw(body)
            return
        if path != "/note":
            return super().do_GET()
        # one note's text: f=<orb>/<relpath>, resolved against that orb's
        # jail. Inside the root, .md only, must exist — anything else 404s.
        rel = (q.get("f") or [""])[0]
        idx, _, rel = rel.partition("/")
        root = orb_root(idx)
        if root is None:
            self.send_response(404)
            self.end_headers()
            return
        target = _jail_resolve(root, rel)
        if target is None or target.suffix != ".md" or not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = target.read_text(encoding="utf-8", errors="replace").encode()
        self._raw(body, ctype="text/plain; charset=utf-8")


if __name__ == "__main__":
    (HERE / "state").mkdir(exist_ok=True)   # the ring's runtime files land here
    port = int(config().get("port", 8794))
    print(f"jarvis up: http://127.0.0.1:{port}/stage.html", flush=True)
    print("  tracker (camera): open that URL in Chrome", flush=True)
    print("  render (overlay): same URL + ?role=render", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
