// js/identity.js — WHO is this face. face-api.js 128-d descriptors, run in a
// Web Worker when the machine allows (OffscreenCanvas + WebGL in a worker),
// on the main thread otherwise, never more than one job at a time. The
// engine no longer detects a face itself — the caller (faces.js) aligns an
// already-tracked face into a 150x150 chip via MediaPipe iris landmarks and
// hands that chip in; describe() runs only faceRecognitionNet on it. The
// pure math (dist / matchScore) is what the policy's `score` comes from.
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

// how long a single "desc" round trip is allowed to take before we give up
// on it (resolve null) rather than let a wedged/dead worker hang describe()
// and its caller's _busy flag forever.
export const JOB_TIMEOUT_MS = 8000;

// real Worker construction, split out so tests can inject a fake worker
// (and a fake canvas) without touching typeof Worker/OffscreenCanvas checks.
const defaultWorkerFactory = (url) => new Worker(url, { type: "module" });

export function createIdentity({ lib = FACE_API_LIB, base = FACE_API_MODELS,
                                 workerUrl = "js/identity.worker.js", forceMain = false,
                                 workerFactory = defaultWorkerFactory,
                                 jobTimeoutMs = JOB_TIMEOUT_MS } = {}) {
  const ID = { mode: "none", error: null, workerError: null, lastJobError: null,
               perf: { ms: 0, n: 0 }, _busy: false, _ready: false };
  let worker = null, faceapi = null, pending = null, seq = 0;

  async function loadMain() {
    faceapi = await import(lib);
    await faceapi.tf.ready();
    await faceapi.nets.faceRecognitionNet.loadFromUri(base);
    ID.mode = "main";
  }
  function loadWorker() {
    return new Promise((resolve, reject) => {
      // the typeof guard only applies to the real Worker/OffscreenCanvas path;
      // a caller-supplied workerFactory (tests) bypasses it deliberately.
      const usingDefaultFactory = workerFactory === defaultWorkerFactory;
      if (usingDefaultFactory && (typeof OffscreenCanvas === "undefined" || typeof Worker === "undefined")) {
        return reject(new Error("no worker canvas"));
      }
      try { worker = workerFactory(workerUrl); }
      catch (e) { return reject(e); }
      const timer = setTimeout(() => reject(new Error("worker load timeout")), 45000);
      worker.onmessage = (e) => {
        const m = e.data;
        if (m.t === "ready") { clearTimeout(timer); ID.mode = "worker"; resolve(); }
        else if (m.t === "fail") { clearTimeout(timer); reject(new Error(m.err)); }
        else if (m.t === "desc" && pending && pending.id === m.id) {
          const p = pending; pending = null; ID._busy = false;
          clearTimeout(p.timer);
          ID.perf.ms = m.ms; ID.perf.n++;
          p.resolve(m.vec ? new Float32Array(m.vec) : null);
        }
        // any other id is a late reply for a job we already gave up on (timed
        // out or superseded) — ignore it, pending no longer points at it.
      };
      // worker.onerror fires both for load-time script errors (loadWorker's
      // own promise is still pending — reject it) and for a worker that dies
      // mid-job after "ready" (fail the in-flight desc instead of leaving it
      // to hang forever).
      worker.onerror = (e) => {
        const msg = (e && e.message) || "worker error";
        if (ID.mode !== "worker") { clearTimeout(timer); reject(new Error(msg)); return; }
        if (pending) {
          const p = pending; pending = null; ID._busy = false; ID.lastJobError = msg;
          clearTimeout(p.timer);
          p.resolve(null);
        }
      };
      worker.onmessageerror = () => {
        if (pending) {
          const p = pending; pending = null; ID._busy = false; ID.lastJobError = "message error";
          clearTimeout(p.timer);
          p.resolve(null);
        }
      };
      worker.postMessage({ t: "load", lib, base });
    });
  }

  ID.ready = (async () => {
    if (!forceMain) {
      try { await loadWorker(); ID._ready = true; return true; }
      catch (e) { ID.workerError = e.message; if (worker) { worker.terminate(); worker = null; } }
    }
    try { await loadMain(); ID._ready = true; ID.error = null; return true; }
    catch (e) { ID.error = "main: " + e.message; ID.mode = "none"; return false; }
  })();
  ID.isReady = () => ID._ready;

  async function describeMain(canvas) {
    // face-api's main-thread input check is `instanceof HTMLCanvasElement`,
    // so an OffscreenCanvas needs copying regardless. But we ALWAYS copy,
    // even when `canvas` already is an HTMLCanvasElement: the caller supplies
    // a face chip; we copy it so a shared/reused source canvas can't change
    // under the await (computeFaceDescriptor yields control — a caller that
    // reuses one canvas across faces, e.g. faces.js's S.chip fallback path,
    // could redraw it for a different face before this read finishes).
    const c = document.createElement("canvas");
    c.width = canvas.width; c.height = canvas.height;
    c.getContext("2d").drawImage(canvas, 0, 0);
    // c is now a fresh, aligned 150x150 chip (faces.js's S.chip) — no
    // detection needed, just the recognition net's descriptor.
    const t0 = performance.now();
    const vec = await faceapi.nets.faceRecognitionNet.computeFaceDescriptor(c);
    ID.perf.ms = performance.now() - t0; ID.perf.n++;
    return vec || null;
  }
  ID.describe = async (canvas) => {
    if (!ID._ready) return null;
    if (ID._busy) throw new Error("busy");
    if (ID.mode !== "worker") {
      ID._busy = true;
      try { return await describeMain(canvas); }
      finally { ID._busy = false; }
    }
    // worker mode: _busy is cleared by exactly one of the paths below — the
    // desc reply (loadWorker's onmessage), the per-job timeout, a dispatch
    // failure right here, or a worker onerror/onmessageerror while a job is
    // in flight. Never left for a generic finally to catch, since a hung
    // worker must not wedge _busy true forever.
    ID._busy = true;
    let bitmap;
    try {
      bitmap = canvas.transferToImageBitmap ? canvas.transferToImageBitmap()
                                            : await createImageBitmap(canvas);
    } catch (e) {
      ID._busy = false; pending = null;
      throw e;
    }
    return await new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(() => {
        if (pending && pending.id === id) {
          const p = pending; pending = null; ID._busy = false; ID.lastJobError = "timeout";
          p.resolve(null);
        }
      }, jobTimeoutMs);
      pending = { id, resolve, timer };
      try {
        worker.postMessage({ t: "desc", id, bitmap }, [bitmap]);
      } catch (e) {
        clearTimeout(timer);
        pending = null; ID._busy = false;
        reject(e);
      }
    });
  };
  return ID;
}
