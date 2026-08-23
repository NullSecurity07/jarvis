// js/identity.worker.js — face-api descriptors off the main thread. The
// caller (faces.js) already aligned the face into a 150x150 chip via
// MediaPipe iris landmarks; this worker just runs faceRecognitionNet on it,
// no detection of its own.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later
let faceapi = null, ready = false;
self.onmessage = async (e) => {
  const m = e.data;
  if (m.t === "load") {
    try {
      faceapi = await import(m.lib);
      // face-api never auto-initializes its env inside a worker (no window,
      // so isNodejs()/isBrowser() both fail) — env.monkeyPatch() throws
      // "environment is not defined" there. setEnv() sets it directly instead.
      faceapi.env.setEnv({
        Canvas: OffscreenCanvas,
        CanvasRenderingContext2D: OffscreenCanvasRenderingContext2D,
        Image: ImageBitmap, ImageData, Video: OffscreenCanvas,
        createCanvasElement: () => new OffscreenCanvas(1, 1),
        createImageElement: () => { throw new Error("no <img> in a worker"); },
        createVideoElement: () => { throw new Error("no <video> in a worker"); },
        fetch: self.fetch.bind(self),
        readFile: () => { throw new Error("no filesystem in a worker"); },
      });
      await faceapi.tf.setBackend("webgl").catch(() => faceapi.tf.setBackend("cpu"));
      await faceapi.tf.ready();
      await faceapi.nets.faceRecognitionNet.loadFromUri(m.base);
      ready = true;
      postMessage({ t: "ready", backend: faceapi.tf.getBackend() });
    } catch (err) {
      postMessage({ t: "fail", err: String((err && err.message) || err) });
    }
    return;
  }
  if (m.t === "desc") {
    // The caller (main thread) runs this job under a timeout and treats a
    // missing reply as a wedged worker — so no matter what goes wrong below
    // (not ready, bad bitmap, detection error, even a failing close/dispose)
    // this handler must still postMessage a {t:"desc", ...} reply.
    const t0 = performance.now();
    let vec = null, tensor = null;
    try {
      if (!ready) throw new Error("not ready");
      const c = new OffscreenCanvas(m.bitmap.width, m.bitmap.height);
      const cx = c.getContext("2d");
      cx.drawImage(m.bitmap, 0, 0);
      // the bitmap is already an aligned 150x150 chip (faces.js's S.chip) —
      // no detection needed. computeFaceDescriptor's dimension code doesn't
      // accept an OffscreenCanvas directly in a worker — feed it a tensor
      // built from ImageData instead.
      const imageData = cx.getImageData(0, 0, c.width, c.height);
      tensor = faceapi.tf.browser.fromPixels(imageData);
      const d = await faceapi.nets.faceRecognitionNet.computeFaceDescriptor(tensor);
      vec = d || null;
    } catch (err) {
      vec = null;
    } finally {
      // close the bitmap on every path, including the "not ready" throw
      // above (previously it threw before the bitmap was ever closed — a
      // leak); guard both cleanups so one failing can't skip the other or
      // skip the reply below.
      try { m.bitmap.close(); } catch { /* already closed or invalid */ }
      try { if (tensor) tensor.dispose(); } catch { /* best-effort cleanup */ }
    }
    postMessage({ t: "desc", id: m.id, vec, ms: performance.now() - t0 });
  }
};
