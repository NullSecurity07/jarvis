// js/identity.js — WHO is this face. face-api.js 128-d descriptors, run in a
// Web Worker when the machine allows (OffscreenCanvas + WebGL in a worker),
// on the main thread otherwise, never more than one job at a time. The pure
// math (dist / matchScore) is what the policy's `score` comes from.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later

export function dist(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) { const d = a[i] - b[i]; s += d * d; }
  return Math.sqrt(s);
}

/** min distance from vec to any enrolled vector; Infinity when none */
export function matchScore(vec, owner) {
  const vecs = (owner && owner.vecs) || [];
  let best = Infinity;
  for (const o of vecs) { const d = dist(vec, o); if (d < best) best = d; }
  return best;
}

export const FACE_API_LIB = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/dist/face-api.esm.js";
export const FACE_API_MODELS = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/model";

export function createIdentity({ lib = FACE_API_LIB, base = FACE_API_MODELS,
                                 workerUrl = "js/identity.worker.js", forceMain = false } = {}) {
  const ID = { mode: "none", error: null, perf: { ms: 0, n: 0 }, _busy: false, _ready: false };
  let worker = null, faceapi = null, pending = null, seq = 0;

  async function loadMain() {
    faceapi = await import(lib);
    await faceapi.tf.ready();
    await Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri(base),
      faceapi.nets.faceLandmark68TinyNet.loadFromUri(base),
      faceapi.nets.faceRecognitionNet.loadFromUri(base),
    ]);
    ID.mode = "main";
  }
  function loadWorker() {
    return new Promise((resolve, reject) => {
      if (typeof OffscreenCanvas === "undefined" || typeof Worker === "undefined") return reject(new Error("no worker canvas"));
      worker = new Worker(workerUrl, { type: "module" });
      const timer = setTimeout(() => reject(new Error("worker load timeout")), 45000);
      worker.onmessage = (e) => {
        const m = e.data;
        if (m.t === "ready") { clearTimeout(timer); ID.mode = "worker"; resolve(); }
        else if (m.t === "fail") { clearTimeout(timer); reject(new Error(m.err)); }
        else if (m.t === "desc" && pending && pending.id === m.id) {
          const p = pending; pending = null; ID._busy = false;
          ID.perf.ms = m.ms; ID.perf.n++;
          p.resolve(m.vec ? new Float32Array(m.vec) : null);
        }
      };
      worker.onerror = (e) => { clearTimeout(timer); reject(new Error(e.message || "worker error")); };
      worker.postMessage({ t: "load", lib, base });
    });
  }

  ID.ready = (async () => {
    if (!forceMain) {
      try { await loadWorker(); ID._ready = true; return true; }
      catch (e) { ID.error = "worker: " + e.message; if (worker) { worker.terminate(); worker = null; } }
    }
    try { await loadMain(); ID._ready = true; ID.error = null; return true; }
    catch (e) { ID.error = (ID.error ? ID.error + "; " : "") + "main: " + e.message; ID.mode = "none"; return false; }
  })();
  ID.isReady = () => ID._ready;

  async function describeMain(canvas) {
    // face-api's main-thread input check is `instanceof HTMLCanvasElement`;
    // an OffscreenCanvas (e.g. handed in from a caller that also supports
    // worker mode) fails that check. Copy onto a real <canvas> first.
    let input = canvas;
    if (typeof HTMLCanvasElement === "undefined" || !(canvas instanceof HTMLCanvasElement)) {
      const c = document.createElement("canvas");
      c.width = canvas.width; c.height = canvas.height;
      c.getContext("2d").drawImage(canvas, 0, 0);
      input = c;
    }
    const t0 = performance.now();
    const r = await faceapi.detectSingleFace(input,
      new faceapi.TinyFaceDetectorOptions({ inputSize: 160, scoreThreshold: 0.3 }))
      .withFaceLandmarks(true).withFaceDescriptor();
    ID.perf.ms = performance.now() - t0; ID.perf.n++;
    return r ? r.descriptor : null;
  }
  ID.describe = async (canvas) => {
    if (!ID._ready) return null;
    if (ID._busy) throw new Error("busy");
    ID._busy = true;
    try {
      if (ID.mode === "worker") {
        const bitmap = canvas.transferToImageBitmap ? canvas.transferToImageBitmap()
                                                    : await createImageBitmap(canvas);
        return await new Promise((resolve) => {
          pending = { id: ++seq, resolve };
          worker.postMessage({ t: "desc", id: pending.id, bitmap }, [bitmap]);
        });
      }
      return await describeMain(canvas);
    } finally { if (ID.mode !== "worker") ID._busy = false; }
  };
  return ID;
}
