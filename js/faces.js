// js/faces.js — THE FACES: MediaPipe FaceLandmarker beside the hands, on
// the same frame. Ids persist across frames (box-centre matching, never
// reused, a short ghost grace), eyes read from the blendshapes, head pose
// from the transformation matrix, and identity scored through the engine
// every idEveryMs per face (a new face at once). Pure numbers out; the
// stage draws and decides.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later
import { matchScore } from "./identity.js";

const MODEL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
const IRIS_L = 468, IRIS_R = 473;            // iris centres in the 478-point mesh

export function createFaces({ FaceLandmarker, vision, identity, tune, owner }) {
  const F = () => tune.face, faces = new Map();
  const S = { perf: { face: 0, id: 0, every: 1 }, error: null, _n: 0, _lm: null, _ready: false, _slow: 0, _fast: 0 };
  let nextId = 0;
  const crop = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(160, 160) : document.createElement("canvas");
  if (!crop.getContext) { crop.width = 160; crop.height = 160; }
  const cropCtx = crop.getContext("2d", { alpha: false });

  S.ready = (async () => {
    try {
      S._lm = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: MODEL, delegate: "GPU" },
        runningMode: "VIDEO", numFaces: 3,
        outputFaceBlendshapes: true, outputFacialTransformationMatrixes: true,
        minFaceDetectionConfidence: 0.5, minFacePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
      });
      S._ready = true; return true;
    } catch (e) { S.error = String((e && e.message) || e); return false; }
  })();

  function poseOf(mat) {            // column-major 4x4 -> yaw/pitch degrees
    if (!mat || !mat.data) return { yaw: 0, pitch: 0 };
    const d = mat.data, deg = 180 / Math.PI;
    return { yaw: Math.atan2(d[8], d[10]) * deg, pitch: Math.asin(Math.max(-1, Math.min(1, -d[9]))) * deg };
  }
  function blendOf(bs, name) {
    const c = bs && bs.categories && bs.categories.find(k => k.categoryName === name);
    return c ? c.score : 0;
  }
  function assign(lms, now) {       // detection index -> stable face id
    const live = [...faces.values()].filter(f => now - f.seenT <= F().graceMs);
    const pairs = [];
    lms.forEach((pts, mi) => {
      let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
      for (const p of pts) { if (p.x < x0) x0 = p.x; if (p.y < y0) y0 = p.y; if (p.x > x1) x1 = p.x; if (p.y > y1) y1 = p.y; }
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, w = x1 - x0;
      for (const f of live) {
        const d = Math.hypot(cx - f.cx, cy - f.cy);
        if (d < Math.max(w, f.w) * 0.8) pairs.push({ mi, id: f.id, d });
      }
    });
    pairs.sort((a, b) => a.d - b.d);
    const ids = new Array(lms.length).fill(null), taken = new Set();
    for (const p of pairs) { if (ids[p.mi] != null || taken.has(p.id)) continue; ids[p.mi] = p.id; taken.add(p.id); }
    return ids.map(id => id == null ? nextId++ : id);
  }
  async function score(f, video, now) {   // one descriptor per face, scheduled
    if (!identity || !identity.isReady() || f._scoring) return;
    const O = owner && owner();
    if (!O || !O.vecs || !O.vecs.length) { f.score = NaN; return; }
    if (f.scoredT && now - f.scoredT < F().idEveryMs) return;
    f._scoring = true;
    try {
      const pad = 0.25, vw = video.videoWidth, vh = video.videoHeight;
      const bx = Math.max(0, (f.box.x - f.box.w * pad) * vw), by = Math.max(0, (f.box.y - f.box.h * pad) * vh);
      const bw = Math.min(vw - bx, f.box.w * (1 + 2 * pad) * vw), bh = Math.min(vh - by, f.box.h * (1 + 2 * pad) * vh);
      const s = Math.max(bw, bh);
      cropCtx.fillStyle = "#000"; cropCtx.fillRect(0, 0, 160, 160);
      cropCtx.drawImage(video, bx, by, s, s, 0, 0, 160, 160);
      const vec = await identity.describe(crop);
      f.scoredT = now; f.lastVec = vec || null;
      if (vec) {
        f._hist = (f._hist || []).concat([matchScore(vec, O)]).slice(-3);
        f.score = Math.min(...f._hist);
      }
      S.perf.id = identity.perf.ms;
    } catch (e) { /* busy: try again next frame */ }
    f._scoring = false;
  }
  S.tick = (src, video, now) => {
    if (!S._ready) return;
    S._n++;
    if (S._n % S.perf.every) return;            // the frame skip
    const t0 = performance.now();
    let res;
    try { res = S._lm.detectForVideo(src, now); } catch (e) { return; }
    S.perf.face = S.perf.face ? 0.9 * S.perf.face + 0.1 * (performance.now() - t0) : performance.now() - t0;
    const lms = res.faceLandmarks || [];
    const ids = assign(lms, now);
    const seen = new Set();
    lms.forEach((pts, mi) => {
      const id = ids[mi]; seen.add(id);
      let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
      for (const p of pts) { if (p.x < x0) x0 = p.x; if (p.y < y0) y0 = p.y; if (p.x > x1) x1 = p.x; if (p.y > y1) y1 = p.y; }
      let f = faces.get(id);
      if (!f) { f = { id, score: NaN, scoredT: 0, blinkT: 0, moveT: now, seenT: 0, _open: true, _yaw: 0, _pitch: 0 }; faces.set(id, f); }
      f.box = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      f.cx = (x0 + x1) / 2; f.cy = (y0 + y1) / 2; f.w = x1 - x0; f.h = y1 - y0;
      f.irisL = { x: pts[IRIS_L].x, y: pts[IRIS_L].y }; f.irisR = { x: pts[IRIS_R].x, y: pts[IRIS_R].y };
      const bs = (res.faceBlendshapes || [])[mi];
      f.eyeOpen = 1 - Math.max(blendOf(bs, "eyeBlinkLeft"), blendOf(bs, "eyeBlinkRight"));
      const closedNow = f.eyeOpen < F().blinkClose;
      if (f._open && closedNow) f._closedT = now;               // eyes shut
      if (!f._open && !closedNow && f._closedT && now - f._closedT < 600) f.blinkT = now;   // a real blink: shut then open within 600ms
      f._open = !closedNow;
      const p = poseOf((res.facialTransformationMatrixes || [])[mi]);
      if (Math.hypot(p.yaw - f._yaw, p.pitch - f._pitch) > F().moveDeg) { f.moveT = now; f._yaw = p.yaw; f._pitch = p.pitch; }
      f.yaw = p.yaw; f.pitch = p.pitch;
      f.attention = Math.abs(p.yaw) < F().attYaw && Math.abs(p.pitch) < F().attPitch && f.eyeOpen > F().eyeOpenMin;
      f.seenT = now; f.seenNow = true;
      score(f, video, now);
    });
    for (const f of faces.values()) {
      if (seen.has(f.id)) continue;
      f.seenNow = false;
      if (now - f.seenT > F().graceMs) faces.delete(f.id);
    }
  };
  S.list = () => [...faces.values()];     // ghosts inside graceMs are still listed (seenNow=false)
  S.setEvery = (n) => { S.perf.every = Math.max(1, n | 0); };
  return S;
}
