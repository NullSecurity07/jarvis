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

Your AI drives the ring by writing tiny files into ./state/ :
  state/state      one word: idle | listening | thinking | speaking
  state/mood.json  {"mood": "green"|"amber"|"red", "ts": <unix time>}
  state/wave.json  {"samples": [0..1 x 64], "ts": <unix time>}
Missing files are fine — the ring just idles.
"""
import collections
import json
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
            "enroll", "guest", "lock")          # THE OWNER LOCK
_MAX_QUEUE = 256        # a hidden board must not grow a silent backlog forever

# ---- THE BOARD FILE: what is on the glass, persisted so a reload (or a
# server restart) brings it back. Written from the heartbeat, at most
# once a second, and only when the scene actually changed.
_BOARD = {"sig": None, "t": 0.0}
_BOARD_TYPES = ("card", "img", "model", "panel")
_BOARD_KEYS = ("type", "title", "body", "src", "mm", "lf", "ex", "rx", "ry",
               "rz", "file", "x", "y", "scale")


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


def owner_validate(obj):
    """{vecs: 1..16 x 128 finite numbers, at} -> cleaned dict, or None."""
    try:
        vecs = obj.get("vecs")
        if not isinstance(vecs, list) or not 1 <= len(vecs) <= 16:
            return None
        clean = []
        for v in vecs:
            if not isinstance(v, list) or len(v) != 128:
                return None
            row = []
            for x in v:
                if isinstance(x, bool):
                    return None
                row.append(float(x))
            if any(x != x or x in (float("inf"), float("-inf")) for x in row):
                return None
            clean.append([round(x, 6) for x in row])
        return {"vecs": clean, "at": float(obj.get("at") or time.time())}
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
        # no-store on the page itself so a plain reload always serves
        # current code (Chrome happily caches through reloads otherwise)
        if self.path.split("?")[0].endswith("stage.html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def log_message(self, *a):
        pass

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

    def do_POST(self):
        global _STATE, _STATE_T, _CMD_SEQ, _ACK
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
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, q = url.path, urllib.parse.parse_qs(url.query)
        cfg = config()
        if path == "/config":
            # the page builds its ring name + orb bloom from this, and
            # takes its camera defaults (res / fps) unless the URL says
            self._json({"name": cfg.get("name", "JARVIS"),
                        "res": cfg.get("res"), "fps": cfg.get("fps"),
                        "orbs": [{"title": o.get("title", "?"),
                                  "kind": o.get("kind", "notes")}
                                 for o in cfg["orbs"]]})
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
        target = (root / rel).resolve()
        if (root not in target.parents) or target.suffix != ".md" \
                or not target.is_file():
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
