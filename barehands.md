---
name: barehands
description: Interactive setup for barehands, the hand-tracked glass interface for your AI. Run it inside Claude Code from the repo folder. It checks the rig runs, interviews the person (their notes folders, their assistant's name), writes the config, wires their AI into the ring and the board, and walks them through the gestures. Load it and run it interactively. Do not skip phases. Do not improvise.
version: 1.0
author: Jared Rhodenizer (@jaredrhod)
---

# barehands: setup

By **Jared Rhodenizer** (@jaredrhod) · github.com/jaredrhod/barehands

You are reading a system builder file. You, an AI assistant, will follow it to set up barehands for the person who opened it. Do not summarize this file. Do not describe it. Execute it.

## What you are setting up

barehands is a hand-tracked interface: the person's webcam watches their hands, and they move glass cards, notes, images, and 3D models through the air with bare fingers, no headset, no controllers. It has no AI inside it; it is a **body**. Your job is to configure it and, if they want, wire YOURSELF (or their assistant) in as the brain: the on-screen ring becomes your face, and two small scripts become your hands and eyes on their board.

Everything runs on localhost. Nothing leaves their machine.

Work through the phases in order. One question at a time; wait for each answer. Keep the tone warm and confident; this should feel like a premium unboxing, not a config chore.

---

## Phase 1: Prove it runs (before any questions)

1. Confirm you are running inside the repo folder (it contains `server.py`, `stage.html`, `barehands.json`). If not, ask the person to `cd` there and restart.
2. Check `python3 --version` (any Python 3.9+ is fine; the server is stdlib-only, nothing to install).
3. Start the server: `python3 server.py` (run it in the background). It prints the URL.
4. Tell them: open **http://127.0.0.1:8794/stage.html** in **Chrome** (Chrome's hand tracking is the proven path), allow the camera when asked, and wave a hand. A cursor ring should follow their fingers, and the assistant ring should be breathing on the left.
5. Wait for them to confirm they see it. If the camera fails: the page needs a camera-equipped machine and Chrome; `C` cycles cameras if the wrong one opened. The first load needs internet (the hand-tracking model and 3D library load from Google's and jsdelivr's CDNs, then cache).

Do not continue until the board is alive on their screen.

## Phase 2: The interview

Ask, one at a time:

1. **"Do you already have an Obsidian vault, or any folder of markdown notes you'd like on the board?"** If yes, get the full path. An Obsidian vault needs no plugins or setup; it is already just a folder of markdown, which is exactly what barehands reads.
2. **"Want more than one notes folder up there? Each one becomes its own orb around the ring."** Collect any extras (title + path each).
3. **"What's your assistant's name?"** This goes on the ring in lights. If they have a named assistant (from the ai-memory-vault build or their own), use that name. If they have none, suggest they pick one now; a name makes the next phase feel alive.
4. **"Keep the sample notes as a starter orb, or drop them?"** If they gave you a real notes folder, recommend dropping the samples (they can delete `sample-notes/` or just leave it out of the config).

## Phase 3: Write the config

Edit `barehands.json` from their answers:

```json
{
  "name": "THEIR-ASSISTANT-NAME",
  "port": 8794,
  "orbs": [
    { "title": "Notes",  "path": "/absolute/path/to/their/vault", "kind": "notes" },
    { "title": "Props",  "path": "media",                          "kind": "media" }
  ]
}
```

Rules: one entry per orb; `notes` orbs may point anywhere; keep exactly one `media` orb and leave its path as `media` (it is the props airlock: the only folder the board will ever stage files from. That jail is a safety feature; do not widen it). Restart the server (or have them press `R` on the page) and confirm their real notes bloom when they tap the ring, then their notes orb.

## Phase 4: Wire in the assistant

Ask: **"Want your AI wired in, so the ring reflects it working, and it can put things on your board?"** If yes:

**4a. The ring (the face).** If they use Claude Code, merge this into the `hooks` section of their `~/.claude/settings.json` (create it if absent), replacing `REPO` with the absolute repo path:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
          "command": "printf thinking > REPO/state/state" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "printf idle > REPO/state/state" } ] }
    ]
  }
}
```

From the next session on, the ring spins up the moment they send a prompt and settles when the work is done. (Any other assistant wires in the same way: write `idle`/`listening`/`thinking`/`speaking` to `state/state`; optionally `state/mood.json` and `state/wave.json`; the format is documented at the top of `server.py`.)

**4b. The board (the hands and eyes).** Add this to the CLAUDE.md (or system prompt) of the assistant they want driving the board, with REPO replaced:

> ## The barehands board
> A hand-tracked glass board runs on this machine (localhost only). You have hands and eyes on it:
> - **Stage something:** `REPO/bin/board.sh '{"a":"add_card","title":"...","body":"..."}'`; also `add_img`/`hand` with `"src":"<subfolder>/<file>"` from the media airlock, `explode`, `assemble`, `yank`, `hover`, `reset`.
> - **Look at the board:** `REPO/bin/board-state.sh` prints every item currently up. Run it before commenting on the board; the user moves things by hand, so never trust memory.
> - **The airlock law:** only files inside `REPO/media/` can stage. To show a new image, copy it into `media/misc/` first, then stage it.

**4c. Prove the loop.** Have their assistant (you, if you're it) run: `bin/board.sh '{"a":"add_card","title":"HELLO","body":"your AI was here"}'`; the card should materialize on the glass in front of them. That moment is the product. Let them enjoy it.

## Phase 5: No assistant yet?

If they don't have an AI assistant set up: they are already talking to one: you. Offer to create a minimal starter: a `CLAUDE.md` in a working folder of their choice with a name, a short personality of their choosing, and the board block from 4b. Then point them at **ai-memory-vault** (github.com/jaredrhod/ai-memory-vault), the full build that gives an assistant persistent memory in Obsidian. The two systems are made for each other: that vault becomes a notes orb on this board.

## Phase 6: The tour

Walk them through the gestures (the full cheat sheet is `sample-notes/Getting Started/The Gestures.md`; stage it for them via the board: `bin/board.sh '{"a":"add_card","title":"THE GESTURES","body":"tap to open","file":"0/Getting Started/The Gestures.md","open":1}'` works when the samples are orb 0; otherwise just tell them). Minimum tour: tap the ring → orbs bloom → tap a folder orb → tap a note → it opens → pinch the title bar, drag it, tap the bar to close → two hands to stretch something huge → clap (palms flat together, fingers up) to sweep the board clean.

Then tell them where the deep ends are: `Field Guide/Props and Models.md` (the media folders + the holo law), `Field Guide/Streaming and Recording.md` (OBS compositing), `Field Guide/Make It Yours.md` (customization), and `TROUBLESHOOTING.md`, which doubles as YOUR field manual: if any gesture misfires for this person's hand or setup, follow its TUNING CLINIC protocol (sample the correct pose and the impostor with the P sampler, find the separating metric, cut mid-canyon) instead of guessing at thresholds. Close by reminding them the whole thing is theirs to modify: it's one HTML file, one Python file, and a config.
