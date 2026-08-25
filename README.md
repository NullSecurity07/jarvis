# jarvis

A hand-tracked glass board: your webcam watches your hands, and notes, images, and 3D models float over the camera feed as glass cards. Pinch them, throw them, stretch them, force-pull them across the room, blow a model apart into its exploded view. No headset, no controllers. And the ring on the left is a face for an AI: any program that writes a file or curls localhost can drive the board.

Runs on a webcam, Chrome, and stdlib Python. Hand tracking by [Google MediaPipe](https://developers.google.com/mediapipe), 3D by [three.js](https://threejs.org), both loaded from CDNs on first run.

## Run it

```
./launch.sh
```

That is the only launcher, on purpose. It starts `server.py` if it isn't up, then opens the board **full screen and click-through**: the glass covers the display, your hands drive the real mouse, and every click passes straight through to whatever is underneath. Behind the glass is the camera mirror — **M** dials it from full strength down to off, so you choose how much of yourself you see while you work.

| | |
|---|---|
| **POINT** (index up, others folded, ~0.7s) | take the mouse |
| **FIST** | give it back (`Esc` always releases) |
| **M** | mirror: full → 60% → 35% → 20% → off |
| **D** | debug overlay |

Run it again while it's open and it just focuses the board instead of opening a second one.

**Click-through is not a detail** — it is what makes controlling anything possible. The board is opaque and on top, so a synthetic click at screen coordinates would otherwise land on the board itself. `tools/clickthrough.py` empties the window's X input shape, so events pass through while the glass keeps compositing above them. The launcher **refuses to leave the board up** if that fails, rather than handing you a full-screen window that swallows every click.

Manual: `python3 server.py`, then open **http://127.0.0.1:8794/stage.html?mode=mirror&hud=1** in Chrome. There is also a rofi/dmenu entry (`jarvis.desktop`).

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
  "fps": 10,
  "minGapMs": 60,
  "maxStreams": 6,
  "shrink": 3,
  "quality": 70,
  "aliveMs": 1500,
  "display": ":0",
  "xauthority": "/run/user/1000/lyxauth"
}
```

`shrink` is an integer box-filter downscale applied before JPEG encoding (`3` turns 1920x1080 into 640x360) and `quality` is the JPEG quality — together they cost about 15 ms and 26 KB per frame, which is what makes ten frames a second across six windows affordable. `aliveMs` bounds how often a live card's window is re-confirmed to still exist while its captures keep succeeding — a failed capture always confirms right away; this just stops a healthy stream from spawning `i3-msg` every single frame on a board meant to stay up all day.

**i3/X11 only. What can be captured is whatever is MAPPED — which is not the same as visible.** Capture goes through `tools/wincap.py`, which asks the compositor for each window's own offscreen pixmap (`XCompositeNameWindowPixmap`), so a window that is **covered** by something else captures perfectly — including covered by the glass board itself. Verified with a green window deliberately buried under a floating blue one, the screen pixel at its centre confirmed blue, and the capture coming back pure green.

What genuinely cannot be captured is an **unmapped** window: i3 unmaps every window on a hidden workspace, and also unmaps tiled windows that a fullscreen sibling covers (`xprop WM_STATE` reports `Withdrawn`), and an unmapped window has no pixels anywhere for any tool to read. So the gate is `WM_STATE`, asked of X directly per window (about 0.03 ms), reported as `capturable` in `/desks` — separately from `visible`, which still means "its workspace is on screen". The two are deliberately different, and the gap between them is the useful part: a window behind the glass is capturable but not visible.

For a window that really is unmapped, a card shows the **last frame it ever produced**, honestly aged: `LIVE` (green), `OFF-SCREEN · Nm ago` (grey), or `OFF-SCREEN · tap to bring` with an empty glass frame if nothing was ever captured. Tapping that badge **brings the window here** — moves it onto the board's own workspace, where the glass floats over it, so it stays mapped and streams genuinely live from then on. That replaced the old `peek`, which could only borrow a single frame at the cost of a visible flicker and went stale immediately. (`peek` still exists as the fallback when the server cannot see a board window to bring things to.) Missing the `desks` key entirely, or `"enabled": false`, disables the feature completely: every `/desks*` endpoint 404s, and the orb never appears.

**Nothing here ever i3-fullscreens the board** — it is a floating move+resize to the screen size. A genuinely fullscreen window makes i3 unmap its tiled siblings, which is exactly the windows you were trying to watch. Floating keeps them mapped. Measured through a real board covering a live-updating terminal: 45 distinct frames in 5 seconds, 38 KB/s, of the terminal's own content.

**Control is limited to i3 commands** — `workspace`, `focus`, `fullscreen`, `peek`, `bring`, `send`, `panel` — sent through a server-side allowlist, never free-form text. The Desks channel itself has no synthetic mouse or keyboard input, and `kill` is not implemented: gesture-driven window control never closes anything for you. (The HUD, below, is a separate feature — a real synthetic pointer, deliberately scoped to move-only, gated by the owner lock, and off unless you turn it on.) The general `move` verb still isn't in the allowlist, and `layout` isn't either — `layout` acts on whatever happens to be focused rather than a named container, with no undo from the board. `bring` and `send` are the narrow, named slice of `move` that the board actually needs, and they are scoped so the **destination is never taken from the request**: `bring` always targets the workspace you are currently looking at (the focused one, as the server itself observed it — the panel is sticky, so its own nominal workspace would be meaningless), and `send` takes a workspace number, which i3 will create if it has to, or a name that must already exist. `panel` only ever resizes the board's own window, the one the server identified. The caller chooses which window moves, never where it can end up. `peek` (above) is the sole exception carved out for refreshing a hidden window, and it's scoped tightly: it only switches to and back from the one workspace the requested window is already on. Desk cards are their own item type, deliberately flat and un-relief'd — a live desktop rendered as an embossed 3D scene would be unreadable, unlike a staged still image.

**Privacy.** Every capture is served from `/desks/shot` and `/desks/stream` on localhost only, exactly like everything else here — nothing leaves your machine. The board's own window (and the status bar) are always excluded from the workspace/window list, so the glass never mirrors itself.

`bin/board.sh '{"a":"desk","xid":N}'` (a specific window) or `'{"a":"desk","ws":1}'` (every window on a workspace) stages a live card from your AI, jailed the same way a tap is — the server re-checks the xid/workspace against the current i3 tree before doing anything.

## The HUD — pointer follow over your real desktop

Your hand's tracked position moves the real system pointer, so you point at your actual desktop the way you'd use a mouse in the air. This is on by default in the panel `./launch.sh` opens — it is `hud=1` on the URL that enables it, **not** the window's shape or size.

There is no separate transparent full-screen mode, and there cannot be one here: **Chrome on this machine will not produce a per-pixel transparent window** — tested with and without `--enable-transparent-visuals`, with and without the GPU; the page always composites opaque white. The mirror panel is the answer to that, not a consolation prize: because it stays mapped it is never de-composited, so it holds full frame rate, and it leaves your actual screen visible to point at.

**One board at a time.** The launcher refuses if a jarvis Chrome is already running, and tells you which pid to kill. This is not fussiness: Chrome flags only land on a genuinely new process, so a second window opened against the same profile is handed to the running browser and keeps *its* flags. `--ignore-gpu-blocklist` disappears, MediaPipe falls back to the CPU delegate, and every model runs 4-6x slower — measured 8-10 fps against 31 fps, on the same machine with the same board. `JARVIS_FORCE=1` overrides it if you insist.

**Enable it** in `jarvis.json`:

```json
"pointer": { "enabled": true, "rateHz": 90 }
```

`enabled: false` (or the key missing entirely) makes `POST /pointer` 404 — the HUD page still opens, but hands only hover, nothing moves. `rateHz` caps how often the server will actually move the pointer (dropped, never queued, so the cursor can't lag behind in a backlog); the page throttles its own sends to the same number.

**Scope: move and click.** `POST /pointer` accepts `{"a":"move",x,y}` and `{"a":"click",button,action}`; scroll, drag and keyboard are still refused with a 400. Click arrived because controlling a desk on the glass is the point of showing it live — the earlier move-only limit was staged caution, not a permanent boundary. It stays narrow: buttons 1-3 only (no wheel — scrolling has its own gesture on the glass, and a stuck synthetic wheel is miserable), `action` is `press`/`release`/`click`, and it is rate-limited by `pointer.clickHz` (default 8/s) so a stuck gesture cannot machine-gun the desktop. A click always happens at the pointer's **current** position — the caller moves first — so no single call means "click over there" at coordinates you never saw the cursor reach. Set `"click": false` in `jarvis.json`'s `pointer` block to go back to look-but-don't-touch.

### Controlling a desk

Stage a window as a live card (Desks orb → tap a window). Opening one **brings that window to the workspace you are on** and gives it room — i3 tiles a newly opened window beside whatever is already there, so it often has only half a screen, and a card shows the window at **life size** (its own pixels, 1:1, exactly as it looks if you switch to it). Close a card with the **✕** on its bar, the **X** key, or `bin/board.sh '{"a":"deskclose"}'`; a plain tap on the bar no longer closes anything, so the bar is safe to grab. Then **POINT** to arm: the card you are aiming at glows amber, and your fingertip drives *that application's* real cursor — the card is a remote control for a window that may be anywhere, including behind the board. **Pinch to click** inside it. Make a **fist** to disarm, and pinch goes back to meaning "grab this card and move it around the glass". One state decides which of the two a pinch means, so there is no third hand shape to learn.

The mapping is exact: a desk card's `<img>` is `max-width`/`max-height` with no `object-fit`, centred by flex, so its bounding box *is* the displayed pixels — letterboxing included — and it is read post-transform, so a scaled or rotated card still maps correctly. Only a card whose window is currently **capturable** can be driven: an unmapped window is showing a frozen last frame, and clicking where something used to be is worse than not clicking at all.

**The pointer law.**
- Pointer control **starts disarmed**, every time, no exceptions. While disarmed, nothing is ever sent to `/pointer`.
- It works in **either** launcher — `hud=1` is what enables it, not the overlay window, so the mirror view drives the pointer just as well as the full-screen glass does.
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
