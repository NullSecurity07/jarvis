// js/identity.worker.js — face-api descriptors off the main thread.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later
let faceapi = null, ready = false;
self.onmessage = async (e) => {
  const m = e.data;
  if (m.t === "load") {
    try {
      faceapi = await import(m.lib);
      if (faceapi.env && faceapi.env.monkeyPatch) {
        faceapi.env.monkeyPatch({
          Canvas: OffscreenCanvas,
          createCanvasElement: () => new OffscreenCanvas(1, 1),
        });
      }
      await faceapi.tf.setBackend("webgl").catch(() => faceapi.tf.setBackend("cpu"));
      await faceapi.tf.ready();
      await Promise.all([
        faceapi.nets.tinyFaceDetector.loadFromUri(m.base),
        faceapi.nets.faceLandmark68TinyNet.loadFromUri(m.base),
        faceapi.nets.faceRecognitionNet.loadFromUri(m.base),
      ]);
      ready = true;
      postMessage({ t: "ready", backend: faceapi.tf.getBackend() });
    } catch (err) {
      postMessage({ t: "fail", err: String((err && err.message) || err) });
    }
    return;
  }
  if (m.t === "desc") {
    const t0 = performance.now();
    let vec = null;
    try {
      if (!ready) throw new Error("not ready");
      const c = new OffscreenCanvas(m.bitmap.width, m.bitmap.height);
      c.getContext("2d").drawImage(m.bitmap, 0, 0);
      m.bitmap.close();
      const r = await faceapi.detectSingleFace(c,
        new faceapi.TinyFaceDetectorOptions({ inputSize: 160, scoreThreshold: 0.3 }))
        .withFaceLandmarks(true).withFaceDescriptor();
      vec = r ? r.descriptor : null;
    } catch (err) { vec = null; }
    postMessage({ t: "desc", id: m.id, vec, ms: performance.now() - t0 });
  }
};
