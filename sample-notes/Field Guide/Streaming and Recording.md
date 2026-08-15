# Streaming and Recording (advanced)

For everyday use you only need the tracker page — full screen, camera on, done. But barehands was born on camera, and it composites beautifully.

## The two-page split

One scene, two views:

- **The tracker** (`stage.html`) owns your camera and physics. This is YOUR monitor.
- **The render page** (`stage.html?role=render`) is camera-free and truly transparent — it mirrors the scene from the server's state bus.

Drop the render URL into an OBS **browser source** over your camera source and OBS composites the glass with real alpha — the cards float over your actual video feed.

## The knobs (URL parameters on the render page)

- `&cursors=0` — hide the finger rings from the broadcast. Bare hands, maximum sorcery.
- `&ss=2` with a 3840×2160 browser source — 2× supersampled rendering, cards stay razor sharp when stretched.
- `&mirror=1` — if your OBS camera source is mirrored, this turns off the render page's default flip.

The render page flips X by default because OBS shows cameras un-mirrored while the tracker works in selfie space — reach left, and the cards go left on the broadcast too.
