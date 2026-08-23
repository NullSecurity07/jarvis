// tools/fixtures/fake-faceapi.mjs — minimal stand-in for face-api.js, used
// only by tools/test_identity.mjs to exercise identity.js's main-thread
// describe() path in Node (no browser, no real face-api/tfjs available).
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later
export const tf = { ready: async () => {} };
export const nets = {
  faceRecognitionNet: {
    loadFromUri: async () => {},
    // yields (setTimeout, not a microtask) before reading input.tag, so a
    // test can mutate the *original* shared canvas in between the
    // describe() call returning its promise and this read — proving
    // whether the caller's input was snapshotted before the await or read
    // live through a shared reference.
    computeFaceDescriptor: async (input) => {
      await new Promise((r) => setTimeout(r, 5));
      return input.tag;
    },
  },
};
