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

A `Desks` orb turns the board into a live view of your own machine — tap it and every workspace unfolds, each with its windows (`class · title`); tap a window to stage it as a desk card, or tap its small **focus** button to switch to it directly. A live card's bar works like a note's: tap once to close it, or double-tap it to focus that window instead. Enable it in `jarvis.json`:

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

**i3/X11 only, and only the visible workspace is ever actually live.** `maim -i <xid>` captures the screen REGION a window occupies, not the window's own contents — on a workspace that isn't currently visible, that region belongs to whatever workspace IS onscreen, so it would silently hand back the wrong window's pixels. And there's no way around it: i3 unmaps a window the moment its workspace is hidden (`xprop WM_STATE` reports `Withdrawn`), and an unmapped window has no backing pixels for anything — `maim`, `import`, `xwd`, all of them — to read. So the board never even tries: `/desks/shot` and `/desks/stream` only call `maim` for a window whose workspace is currently visible (`capturable` in the `/desks` response). For everything else, a card shows the **last frame that window ever produced**, honestly aged — its badge reads `LIVE` (green) while the workspace is visible, `HIDDEN · Nm ago` (grey) while showing that cached frame, or `HIDDEN · no view yet` with an empty glass frame if nothing was ever captured. The one way to refresh a hidden window is the card's small **refresh** tap: it switches to that workspace, waits for the window to paint, captures, and switches straight back — a real flicker on your actual screen, which is why it's tap-only, never automatic and never on a timer. Missing the `desks` key entirely, or `"enabled": false`, disables the feature completely: every `/desks*` endpoint 404s, and the orb never appears.

**Control is limited to i3 commands** — `workspace`, `focus`, `fullscreen`, `peek` — sent through a server-side allowlist, never free-form text. The Desks channel itself has no synthetic mouse or keyboard input, and `kill` is not implemented: gesture-driven window control never closes anything for you. (The HUD, below, is a separate feature — a real synthetic pointer, deliberately scoped to move-only, gated by the owner lock, and off unless you turn it on.) `move` and `layout` aren't in the allowlist either — the page never calls them, and both act on more than "this one window" (`move` relocates a whole container between workspaces; `layout` re-lays-out whatever's currently focused) with no undo from the board, so they stay out until something actually needs them. `peek` (above) is the sole exception carved out for refreshing a hidden window, and it's scoped tightly: it only switches to and back from the one workspace the requested window is already on. Desk cards are their own item type, deliberately flat and un-relief'd — a live desktop rendered as an embossed 3D scene would be unreadable, unlike a staged still image.

**Privacy.** Every capture is served from `/desks/shot` and `/desks/stream` on localhost only, exactly like everything else here — nothing leaves your machine. The board's own window (and the status bar) are always excluded from the workspace/window list, so the glass never mirrors itself.

`bin/board.sh '{"a":"desk","xid":N}'` (a specific window) or `'{"a":"desk","ws":1}'` (every window on a workspace) stages a live card from your AI, jailed the same way a tap is — the server re-checks the xid/workspace against the current i3 tree before doing anything.

## The HUD — pointer follow over your real desktop

Mirroring a desktop onto the glass turned out to be impossible on X11 (i3 unmaps any window that isn't currently visible, so there are no pixels left for anything to capture — see the Desks section above). The HUD is the other way around: instead of showing your desktop on the glass, it puts the glass **on top of** your desktop. Your real browser, terminal, whatever's open — stays exactly where it is, fully visible, and the board's ring + glass items float above it on a transparent window. Your hand's tracked position drives the real system pointer, so you point at your actual desktop the way you'd use a mouse in the air.

```
./launch-hud.sh
```

This opens `stage.html?mode=overlay&hud=1` in its own transparent, click-through, borderless, fullscreen Chrome window (floated and stickied across every workspace by i3), starting the server first if it isn't already up.

**Enable it** in `jarvis.json`:

```json
"pointer": { "enabled": true, "rateHz": 90 }
```

`enabled: false` (or the key missing entirely) makes `POST /pointer` 404 — the HUD page still opens, but hands only hover, nothing moves. `rateHz` caps how often the server will actually move the pointer (dropped, never queued, so the cursor can't lag behind in a backlog); the page throttles its own sends to the same number.

**Scope, this round: FOLLOW ONLY.** No click, no scroll, no drag, no keyboard — `POST /pointer` accepts `{"a":"move",...}` and refuses everything else with a 400. Clicking is a deliberate later step, once the tracking itself feels right.

**The pointer law.**
- Pointer control **starts disarmed**, every time, no exceptions. While disarmed, nothing is ever sent to `/pointer`.
- Arm it with the **`A`** key, or by **pointing** — index finger up, the other fingers folded — and holding for about 0.7 seconds. Release it by making a **fist** and holding, or with `A`, or with `Escape` (which always disarms, instantly, whatever else is happening). The two gestures are deliberately asymmetric: pointing only ever arms and a fist only ever releases, because pointing is also the posture you hold while driving the cursor — if it toggled, you would disarm yourself moments after arming. The cursor follows your **index fingertip**. Each gesture needs its hold, so a flash of either shape does nothing.
- **The owner lock gates every send**: a move is only ever sent for a hand whose role is `owner`, while the lock reads unlocked. A guest's hand, a stranger's hand, or a locked board never drives the pointer — and if the lock flips to locked while armed, the HUD disarms itself instantly, the same frame.
- **`Escape` disarms instantly, always**, independent of everything else.
- The badge at the top of the screen never leaves you guessing: **POINTER IDLE** normally, **POINTER ARMED** (warm/amber, pulsing) whenever a hand could actually be moving your mouse right now.

**Which hand drives:** the owner's first tracked hand; if the owner has two hands in frame, whichever one is *not* currently pinching (so mid-grab on one hand never fights the pointer on the other). The position sent is the same One-Euro-filtered, lerp-smoothed cursor position the board already computes for that hand — nothing new to tune.

**Watch this while they're driving it live:** the badge (armed vs idle) and the real system pointer itself — if the badge says ARMED and the cursor isn't tracking the hand, or the badge is wrong about which state you're in, that's the bug to chase first, before anything gesture-shaped.

`state/state`'s heartbeat (and `/state`) carries `hud: {on, armed, hz}` so this is checkable without a camera — from another terminal, `curl -s 127.0.0.1:8794/state | python3 -c "import json,sys;print(json.load(sys.stdin).get('hud'))"` (only meaningful while the tracker page is open and pushing).

## Streaming

`stage.html?role=render` is a camera-free, truly transparent mirror of the scene for an OBS browser source (`&cursors=0` hides the finger rings, `&ss=2` renders at 2×, `?portrait=1` on the tracker for 9:16). The board is also persisted (`state/board.json`) and restored after a reload — `?fresh=1` boots empty.

## License

AGPL-3.0-or-later. Based on work by Jared Rhodenizer (AGPL-3.0); the fork is by nullsec. See [LICENSE](LICENSE).
