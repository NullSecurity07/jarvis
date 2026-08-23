# jarvis

A hand-tracked glass board: your webcam watches your hands, and notes, images, and 3D models float over the camera feed as glass cards. Pinch them, throw them, stretch them, force-pull them across the room, blow a model apart into its exploded view. No headset, no controllers. And the ring on the left is a face for an AI: any program that writes a file or curls localhost can drive the board.

Runs on a webcam, Chrome, and stdlib Python. Hand tracking by [Google MediaPipe](https://developers.google.com/mediapipe), 3D by [three.js](https://threejs.org), both loaded from CDNs on first run.

## Run it

```
./launch.sh
```

That starts `server.py` (if it isn't already up) and opens the board in its own Chrome profile (`~/.local/share/jarvis/chrome`) with the flags that keep the tracker running on any workspace. Manual: `python3 server.py`, then open **http://127.0.0.1:8794/stage.html** in Chrome. There is also a rofi/dmenu entry (`jarvis.desktop`).

Tap the ring → orbs bloom → tap an orb → your files unfold on glass.

## Config — `jarvis.json`

```json
{
  "name": "JARVIS",
  "port": 8794,
  "res": "1280x720",
  "fps": "auto",
  "orbs": [
    { "title": "Notes", "path": "~/Documents/notes", "kind": "notes" },
    { "title": "Props", "path": "media",             "kind": "media" }
  ]
}
```

- `orbs`: one line per orb. `notes` orbs point at any folder of markdown (an Obsidian vault works as-is). Keep exactly one `media` orb at `media/` — the **airlock**: the only folder the board will ever stage files from (`media/misc/` images, `media/fx/` transparent props, `media/models/` 3D, `media/holo/` ghost-glass holograms).
- `res`: the tracker's capture resolution. This laptop's camera is 1280×720 max (MJPG 30 fps; the 1080p default would negotiate 10 fps YUYV).
- `fps`: `auto` | `fast` | `light` — the light governor's default (see below).
- The server re-reads this file whenever it changes; no restart. (`R` on the board re-reads the notes folders.)

## Browse your files

A `files` orb browses any folder on disk — not just notes and the media airlock: PDFs, code, text, archives, anything. Add it to `jarvis.json`:

```json
"files": {
  "roots": [
    { "title": "Documents", "path": "~/Documents" },
    { "title": "Desktop",   "path": "~/Desktop" }
  ],
  "maxBytes": 52428800
}
```

Missing the key entirely = one `Documents` root at `~/Documents`, 50MB cap (`~` itself is deliberately never a default or example root — it would hand out `~/.ssh` and everything else in your home directory; point a root at `~` yourself if you really want that). Each root becomes its own orb; tap one and its folder listing unfolds the same glass pane as a notes orb, with an `..` row to go back up. Every row is tagged with its kind (`PDF`, `IMG`, `TXT`, `MP4`, …) and opens accordingly: markdown in the note panel, text/code in a monospace panel (capped at 200KB), images as 3D reliefs, PDFs in a pdf.js panel (the same scroll gesture that pages a note turns PDF pages), video/audio in a controls card, `.glb`/`.gltf` as a hologram, anything else as a plain "here's what this is" card — nothing unrecognized ever gets dumped raw into the page.

**Read-only.** The server only ever opens files for reading, jailed to the configured roots exactly like a notes orb (symlink escapes and `..` are rejected by resolving the real path and checking it's still inside the root; every dot-prefixed path segment — `.ssh`, `.bash_history`, `.config`, `.git`, ... — is refused outright, wherever it would land) — there is no write, delete, or execute path anywhere in `/files` or `/file`. A root you don't add is never reachable; you're opting a folder in by naming it here.

## Tuning — `tuning.json`

Every gesture and camera number the board uses lives in `tuning.json` and is read at boot (`GET /tuning`): detector confidences, the One-Euro smoothing, the pinch's three signatures and its touch ceilings, the world lane, tap/fling feel, claw timing, clap, the light governor. Edit it, then `bin/board.sh '{"a":"reload"}'`. Each block's `_` string says what the numbers mean and what they were fitted from. The method for fitting them to a hand is in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (the P sampler, the K clinic, the tape + `tools/replay.py`).

## The gestures

Tap (quick pinch) opens and closes. Pinch-drag moves. Hold still while carrying to rotate in 3D. Two hands scale. Flick to throw. **Clap** (palms together, fingers up, or a real strike that vanishes both hands) sweeps the board clean. **The claw**: flash your hand open, then freeze thumb+pinky and half-hook the other three with the C facing the camera, aim at something, let it strain (~1 s), snap shut, and it rips into your hand. An empty pinch dragged sideways scrubs a 3D model's exploded view.

Keys on the tracker: `D` debug overlay · `P` pose sampler · `K` camera clinic · `L` light governor (auto/fast/light) · `C` cycle cameras · `R` respawn.

## Wire in an AI

- **The ring is a face.** Write `thinking` / `idle` / `listening` / `speaking` to `state/state` and the ring reacts (`state/mood.json`, `state/wave.json` optional; format at the top of `server.py`). Claude Code: a `UserPromptSubmit` hook writing `thinking` and a `Stop` hook writing `idle`.
- **The board is a stage.** `bin/board.sh '{"a":"present","title":"THE PLAN","body":"..."}'` flies it center stage, spotlit. Also `add_card`, `add_img`/`hand` (`"src":"misc/x.png"` from the airlock), `open_file` (`{"root":0,"rel":"notes/todo.txt"}` — opens a file from a configured [files root](#browse-your-files), jailed the same way a tap is), `desk` (`{"xid":N}` or `{"ws":N}` — stages a live [i3 window or workspace](#your-desktops-on-the-glass), jailed to the current i3 tree), `explode`, `assemble`, `yank`, `hover`, `reset`, `clear`, `clinic`, `light`, `dump`, `reload`. The server enforces the action allowlist and the media jail. `board.sh` prints **`landed #id`** when the tracker actually applied the command, or `queued` with the board's freshness (`--wait ms` to block longer).
- **`bin/board-state.sh`** prints what is on the board right now.
- **`GET /state`** → `perf` (fps, inference ms, camera governor, clinic table), cursors, items, `ack`.

## The owner lock (face + eyes)

The board belongs to one face. On first boot, the one face alone in front of the camera for ~3 s is enrolled as the owner (`state/owner.json`, kept forever — delete it to re-learn). After that:

- **You in frame, eyes live** (open, a blink or head movement in the last 8 s) → the board is yours, exactly as before.
- **You away** → LOCKED: every gesture is ignored, the ring says LOCKED, the glass frosts so nothing can be read. Unlocks the moment you're back.
- **Someone else** → red ring on the self-view, their hands do nothing. A face *very* close to yours (within 1.5× your face width) gets a **GUEST?** chip: tap it with your pinch or hold a thumbs-up 1 s to let them in; they stay in only while they stay that close (10 s of drifting away = revoked). Guests never unlock the board on their own.
- Numbers: `tuning.json → owner` / `face` (`matchDist` is the identity bar; the D overlay shows every face's live score). `?lock=0` on the URL, or `bin/board.sh '{"a":"lock","on":0}'`, overrides the lock locally; `{"a":"guest","allow":1|0}` and `{"a":"enroll"}` (forget the owner) are the other commands; `bin/board-state.sh` prints the lock state and every face first.
- Identity runs in the page (face-api.js in a Web Worker, models from jsdelivr on first run, cached after) — nothing leaves the machine. If the model can't load, an enrolled board stays LOCKED and says why.
- Measured on this rig: identity is ~20 ms per descriptor (in the worker, off the render thread); the owner's live match score sits 0.22–0.45 against the default `matchDist` 0.5; the face tracker itself costs ~12–15 ms/frame and the stage automatically halves its rate if hands+face together exceed the frame budget. Lock engages ~1.5 s after the owner leaves frame and clears on the next 2 live frames back.

## Your desktops on the glass

A `Desks` orb turns the board into a live view of your own machine — tap it and every workspace unfolds, each with its windows (`class · title`); tap a window to stage it as a LIVE card (an actual updating desktop, not a snapshot), or tap its small **focus** button to switch to it directly. A live card's bar works like a note's: tap once to close it, or double-tap it to focus that window instead. Enable it in `jarvis.json`:

```json
"desks": {
  "enabled": true,
  "fps": 4,
  "minGapMs": 180,
  "maxStreams": 3,
  "aliveMs": 1500,
  "display": ":0",
  "xauthority": "/run/user/1000/lyxauth"
}
```

`aliveMs` bounds how often a live card's window is re-confirmed to still exist while its captures keep succeeding — a failed capture always confirms right away; this just stops a healthy stream from spawning `i3-msg` every single frame on a board meant to stay up all day.

**i3/X11 only.** Workspaces and windows come from `i3-msg`; captures come from `maim`, which can grab any window — including ones on a workspace that isn't currently visible — without switching to it first. Missing the `desks` key entirely, or `"enabled": false`, disables the feature completely: every `/desks*` endpoint 404s, and the orb never appears.

**Control is limited to i3 commands** — `workspace`, `focus`, `move`, `layout`, `fullscreen` — sent through a server-side allowlist, never free-form text. There's no synthetic mouse or keyboard input (this machine doesn't have `xdotool`), and `kill` is not implemented: gesture-driven window control never closes anything for you. Desk cards are their own item type, deliberately flat and un-relief'd — a live desktop rendered as an embossed 3D scene would be unreadable, unlike a staged still image.

**Privacy.** Every capture is served from `/desks/shot` and `/desks/stream` on localhost only, exactly like everything else here — nothing leaves your machine. The board's own window (and the status bar) are always excluded from the workspace/window list, so the glass never mirrors itself.

`bin/board.sh '{"a":"desk","xid":N}'` (a specific window) or `'{"a":"desk","ws":1}'` (every window on a workspace) stages a live card from your AI, jailed the same way a tap is — the server re-checks the xid/workspace against the current i3 tree before doing anything.

## Streaming

`stage.html?role=render` is a camera-free, truly transparent mirror of the scene for an OBS browser source (`&cursors=0` hides the finger rings, `&ss=2` renders at 2×, `?portrait=1` on the tracker for 9:16). The board is also persisted (`state/board.json`) and restored after a reload — `?fresh=1` boots empty.

## License

AGPL-3.0-or-later. Based on work by Jared Rhodenizer (AGPL-3.0); the fork is by nullsec. See [LICENSE](LICENSE).
