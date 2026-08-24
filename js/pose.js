// js/pose.js — THE POSE: MediaPipe PoseLandmarker beside the faces and
// hands, on the same frame. Turns "whose hand is this" from a distance
// guess into a skeleton fact — js/owner.js's decide() consumes list() as
// its optional `poses` input. Ids persist across frames (nose-distance
// matching, a short ghost grace — the same convention as js/faces.js's
// assign()). Pure numbers out; the stage decides who's who.
//
// THE FRAMING PROBLEM (Task 21): MediaPipe Pose happily invents off-frame
// joints with a low visibility (and presence) score instead of omitting
// them. A derived value (shoulderW/headW/torso/prop) computed from an
// invented joint is a plausible-looking LIE, so every landmark carries its
// own `ok` (usable: visible enough AND actually inside the frame, not a
// guess past the edge) and every derived value is either a number or
// `null` — never a number built on an unusable landmark. `frame` says
// which body regions were actually usable this tick (explainable in the D
// overlay and the tape); `scaleSrc` says which reference `scale` came
// from, in priority order, so there's always one if any of the body is.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later

const MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";
// BlazePose 33-point indices (MediaPipe canonical)
const NOSE = 0, EAR_L = 7, EAR_R = 8, SHOULDER_L = 11, SHOULDER_R = 12,
      WRIST_L = 15, WRIST_R = 16, HIP_L = 23, HIP_R = 24;
const GRACE_MS = 300;      // a vanished body stays a ghost this long (THE FACES convention)
const MATCH_MULT = 0.8;    // re-match radius, x scale (THE FACES uses x face width)
const MIN_MATCH_R = 0.1;   // floor for the re-match radius before a scale read exists
const EDGE = 0.02;         // a landmark this far outside [0,1] is still "in frame" (lens/crop slop)
// "full" vs "half/stomach-up" both leave hips `ok`, so the split isn't a
// validity gate (both are trustworthy enough to compute torso) — it's
// purely descriptive, using a stricter visibility band than the ok floor.
const FULL_VIS_MARGIN = 0.2;

export function createPose({ PoseLandmarker, vision, tune }) {
  const bodies = new Map();
  const visMin = (tune && tune.pose && typeof tune.pose.visMin === "number") ? tune.pose.visMin : 0.5;
  const S = { perf: { pose: 0, every: Math.max(1, ((tune && tune.pose && tune.pose.everyNthFrame) | 0) || 2) },
              error: null, _n: 0, _lm: null, _ready: false };
  let nextId = 0;

  S.ready = (async () => {
    try {
      S._lm = await PoseLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
        runningMode: "VIDEO", numPoses: 2,
        minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
      });
      S._ready = true; return true;
    } catch (e) { S.error = String((e && e.message) || e); return false; }
  })();

  function pt(lm, i) {              // one landmark -> {x,y,vis,ok}; ok = confident AND actually in frame
    const p = lm[i];
    const vis = typeof p.visibility === "number" ? p.visibility : 1;
    const pres = typeof p.presence === "number" ? p.presence : 1;
    const inFrame = p.x >= -EDGE && p.x <= 1 + EDGE && p.y >= -EDGE && p.y <= 1 + EDGE;
    return { x: p.x, y: p.y, vis, ok: vis >= visMin && pres >= visMin && inFrame };
  }
  function assign(noses, now) {     // detection index -> stable body id, nose-distance matched
    const live = [...bodies.values()].filter(b => now - b.seenT <= GRACE_MS);
    const pairs = [];
    noses.forEach((n, mi) => {
      for (const b of live) {
        const d = Math.hypot(n.x - b.nose.x, n.y - b.nose.y);
        const r = Math.max(b.scale || 0, MIN_MATCH_R) * MATCH_MULT;
        if (d < r) pairs.push({ mi, id: b.id, d });
      }
    });
    pairs.sort((a, b) => a.d - b.d);
    const ids = new Array(noses.length).fill(null), taken = new Set();
    for (const p of pairs) { if (ids[p.mi] != null || taken.has(p.id)) continue; ids[p.mi] = p.id; taken.add(p.id); }
    return ids.map(id => id == null ? nextId++ : id);
  }
  // scale reference, priority order, so there's ALWAYS one if the body is
  // visible at all: shoulderW -> headW (ears) -> the tracked face's own
  // width (FACES already computed it; matched by nose proximity, same
  // convention as stage.html's enrollTick).
  function scaleOf(shoulderW, headW, nose, faces) {
    if (shoulderW != null) return { scale: shoulderW, scaleSrc: "shoulder" };
    if (headW != null) return { scale: headW, scaleSrc: "head" };
    if (nose.ok && faces && faces.length) {
      let best = null, bd = Infinity;
      for (const f of faces) {
        if (!f.seenNow) continue;
        const d = Math.hypot(nose.x - f.cx, nose.y - f.cy);
        if (d < 1.2 * Math.max(f.w, 1e-3) && d < bd) { bd = d; best = f; }
      }
      if (best) return { scale: best.w, scaleSrc: "face" };
    }
    return { scale: null, scaleSrc: null };
  }
  function frameOf(shoulderOk, hipsOk, hipL, hipR, headOk) {
    if (hipsOk) return Math.min(hipL.vis, hipR.vis) >= visMin + FULL_VIS_MARGIN ? "full" : "half";
    if (shoulderOk) return "chest";
    if (headOk) return "shoulder";
    return "unknown";
  }
  S.tick = (src, now, faces) => {
    if (!S._ready) return;
    S._n++;
    if (S._n % S.perf.every) return;           // the frame skip
    const t0 = performance.now();
    let res;
    try { res = S._lm.detectForVideo(src, now); } catch (e) { return; }
    S.perf.pose = S.perf.pose ? 0.9 * S.perf.pose + 0.1 * (performance.now() - t0) : performance.now() - t0;
    const lmsAll = res.landmarks || [];
    const noses = lmsAll.map(lm => ({ x: lm[NOSE].x, y: lm[NOSE].y }));
    const ids = assign(noses, now);
    const seen = new Set();
    lmsAll.forEach((lm, mi) => {
      const id = ids[mi]; seen.add(id);
      let b = bodies.get(id);
      if (!b) { b = { id }; bodies.set(id, b); }
      b.nose = pt(lm, NOSE);
      b.wristL = pt(lm, WRIST_L); b.wristR = pt(lm, WRIST_R);
      b.shoulderL = pt(lm, SHOULDER_L); b.shoulderR = pt(lm, SHOULDER_R);
      const earL = pt(lm, EAR_L), earR = pt(lm, EAR_R);
      b.ears = { L: earL, R: earR };
      const hipL = pt(lm, HIP_L), hipR = pt(lm, HIP_R);
      b.hipL = hipL; b.hipR = hipR;

      const shoulderOk = b.shoulderL.ok && b.shoulderR.ok;
      const hipsOk = hipL.ok && hipR.ok;
      const earsOk = earL.ok && earR.ok;
      const headOk = earsOk || b.nose.ok;

      b.shoulderW = shoulderOk ? Math.hypot(b.shoulderL.x - b.shoulderR.x, b.shoulderL.y - b.shoulderR.y) : null;
      b.headW = earsOk ? Math.hypot(earL.x - earR.x, earL.y - earR.y) : null;
      if (shoulderOk && hipsOk) {
        const smx = (b.shoulderL.x + b.shoulderR.x) / 2, smy = (b.shoulderL.y + b.shoulderR.y) / 2;
        const hmx = (hipL.x + hipR.x) / 2, hmy = (hipL.y + hipR.y) / 2;
        b.torso = Math.hypot(smx - hmx, smy - hmy);
      } else b.torso = null;
      b.prop = (b.shoulderW != null && b.headW != null) ? b.shoulderW / Math.max(b.headW, 1e-6) : null;

      const sc = scaleOf(b.shoulderW, b.headW, b.nose, faces);
      b.scale = sc.scale; b.scaleSrc = sc.scaleSrc;
      b.frame = frameOf(shoulderOk, hipsOk, hipL, hipR, headOk);

      b.seenT = now; b.seenNow = true;
    });
    for (const b of bodies.values()) {
      if (seen.has(b.id)) continue;
      b.seenNow = false;
      if (now - b.seenT > GRACE_MS) bodies.delete(b.id);
    }
  };
  S.list = () => [...bodies.values()];    // ghosts inside GRACE_MS are still listed (seenNow=false)
  S.setEvery = (n) => { S.perf.every = Math.max(1, n | 0); };
  return S;
}
