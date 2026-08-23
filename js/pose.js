// js/pose.js — THE POSE: MediaPipe PoseLandmarker beside the faces and
// hands, on the same frame. Turns "whose hand is this" from a distance
// guess into a skeleton fact — js/owner.js's decide() consumes list() as
// its optional `poses` input. Ids persist across frames (nose-distance
// matching, a short ghost grace — the same convention as js/faces.js's
// assign()). Pure numbers out; the stage decides who's who.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later

const MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";
// BlazePose 33-point indices (MediaPipe canonical)
const NOSE = 0, EAR_L = 7, EAR_R = 8, SHOULDER_L = 11, SHOULDER_R = 12,
      WRIST_L = 15, WRIST_R = 16, HIP_L = 23, HIP_R = 24;
const GRACE_MS = 300;      // a vanished body stays a ghost this long (THE FACES convention)
const MATCH_MULT = 0.8;    // re-match radius, x shoulderW (THE FACES uses x face width)
const MIN_MATCH_R = 0.1;   // floor for the re-match radius before a shoulderW read exists

export function createPose({ PoseLandmarker, vision, tune }) {
  const bodies = new Map();
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

  function pt(lm, i) {
    const p = lm[i];
    return { x: p.x, y: p.y, vis: typeof p.visibility === "number" ? p.visibility : 1 };
  }
  function assign(noses, now) {     // detection index -> stable body id, nose-distance matched
    const live = [...bodies.values()].filter(b => now - b.seenT <= GRACE_MS);
    const pairs = [];
    noses.forEach((n, mi) => {
      for (const b of live) {
        const d = Math.hypot(n.x - b.nose.x, n.y - b.nose.y);
        const r = Math.max(b.shoulderW || 0, MIN_MATCH_R) * MATCH_MULT;
        if (d < r) pairs.push({ mi, id: b.id, d });
      }
    });
    pairs.sort((a, b) => a.d - b.d);
    const ids = new Array(noses.length).fill(null), taken = new Set();
    for (const p of pairs) { if (ids[p.mi] != null || taken.has(p.id)) continue; ids[p.mi] = p.id; taken.add(p.id); }
    return ids.map(id => id == null ? nextId++ : id);
  }
  S.tick = (src, now) => {
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
      b.shoulderW = Math.hypot(b.shoulderL.x - b.shoulderR.x, b.shoulderL.y - b.shoulderR.y);
      const earL = pt(lm, EAR_L), earR = pt(lm, EAR_R);
      b.headW = Math.hypot(earL.x - earR.x, earL.y - earR.y);
      const hipL = pt(lm, HIP_L), hipR = pt(lm, HIP_R);
      const smx = (b.shoulderL.x + b.shoulderR.x) / 2, smy = (b.shoulderL.y + b.shoulderR.y) / 2;
      const hmx = (hipL.x + hipR.x) / 2, hmy = (hipL.y + hipR.y) / 2;
      b.torso = Math.hypot(smx - hmx, smy - hmy);
      b.prop = b.shoulderW / Math.max(b.headW, 1e-6);
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
