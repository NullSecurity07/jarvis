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

- `orbs`: one line per orb. `notes` orbs point at any folder of markdown (an Obsidian vault works as-is). Keep exactly one `media` orb at `media/` — the **airlock**: the only folder the board will ever stage files from (`media/misc/` images, `media/fx/` transparent props, `media/models/` 3D, `media/holo/` hologram wireframes).
- `res`: the tracker's capture resolution. This laptop's camera is 1280×720 max (MJPG 30 fps; the 1080p default would negotiate 10 fps YUYV).
- `fps`: `auto` | `fast` | `light` — the light governor's default (see below).
- The server re-reads this file whenever it changes; no restart. (`R` on the board re-reads the notes folders.)

## Tuning — `tuning.json`

Every gesture and camera number the board uses lives in `tuning.json` and is read at boot (`GET /tuning`): detector confidences, the One-Euro smoothing, the pinch's three signatures and its touch ceilings, the world lane, tap/fling feel, claw timing, clap, the light governor. Edit it, then `bin/board.sh '{"a":"reload"}'`. Each block's `_` string says what the numbers mean and what they were fitted from. The method for fitting them to a hand is in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (the P sampler, the K clinic, the tape + `tools/replay.py`).

## The gestures

Tap (quick pinch) opens and closes. Pinch-drag moves. Hold still while carrying to rotate in 3D. Two hands scale. Flick to throw. **Clap** (palms together, fingers up, or a real strike that vanishes both hands) sweeps the board clean. **The claw**: flash your hand open, then freeze thumb+pinky and half-hook the other three with the C facing the camera, aim at something, let it strain (~1 s), snap shut, and it rips into your hand. An empty pinch dragged sideways scrubs a 3D model's exploded view.

Keys on the tracker: `D` debug overlay · `P` pose sampler · `K` camera clinic · `L` light governor (auto/fast/light) · `C` cycle cameras · `R` respawn.

## Wire in an AI

- **The ring is a face.** Write `thinking` / `idle` / `listening` / `speaking` to `state/state` and the ring reacts (`state/mood.json`, `state/wave.json` optional; format at the top of `server.py`). Claude Code: a `UserPromptSubmit` hook writing `thinking` and a `Stop` hook writing `idle`.
- **The board is a stage.** `bin/board.sh '{"a":"present","title":"THE PLAN","body":"..."}'` flies it center stage, spotlit. Also `add_card`, `add_img`/`hand` (`"src":"misc/x.png"` from the airlock), `explode`, `assemble`, `yank`, `hover`, `reset`, `clear`, `clinic`, `light`, `dump`, `reload`. The server enforces the action allowlist and the media jail. `board.sh` prints **`landed #id`** when the tracker actually applied the command, or `queued` with the board's freshness (`--wait ms` to block longer).
- **`bin/board-state.sh`** prints what is on the board right now.
- **`GET /state`** → `perf` (fps, inference ms, camera governor, clinic table), cursors, items, `ack`.

## Streaming

`stage.html?role=render` is a camera-free, truly transparent mirror of the scene for an OBS browser source (`&cursors=0` hides the finger rings, `&ss=2` renders at 2×, `?portrait=1` on the tracker for 9:16). The board is also persisted (`state/board.json`) and restored after a reload — `?fresh=1` boots empty.

## License

AGPL-3.0-or-later. Based on work by Jared Rhodenizer (AGPL-3.0); the fork is by nullsec. See [LICENSE](LICENSE).
