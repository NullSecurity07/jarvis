// tools/test_owner.mjs — run: node --test tools/test_owner.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, TUNE_OWNER_DEFAULTS, TUNE_FACE_DEFAULTS } from "../js/owner.js";

const T = { owner: { ...TUNE_OWNER_DEFAULTS }, face: { ...TUNE_FACE_DEFAULTS } };
const owner = (o = {}) => ({ id: 1, cx: 0.5, cy: 0.4, w: 0.2, score: 0.3, eyeOpen: 0.9, blinkT: 900, moveT: 900, ...o });
const other = (o = {}) => ({ id: 2, cx: 0.9, cy: 0.4, w: 0.18, score: 0.9, eyeOpen: 0.9, blinkT: 900, moveT: 900, ...o });
const hand = (o = {}) => ({ id: 10, wx: 0.5, wy: 0.7, span: 0.12, ...o });
const base = (o = {}) => ({ now: 1000, enabled: true, enrolled: true, identityReady: true, lockOverride: false,
                            faces: [], hands: [], approvals: {}, ...o });
// run the policy N frames, 33ms apart, carrying state; returns the last verdict
function run(inputFn, n, prev = {}) {
  let v = null;
  for (let k = 0; k < n; k++) { v = decide(inputFn(1000 + k * 33), T, prev); prev = v.state; }
  return v;
}

test("unenrolled: lock disabled, every hand is the owner's", () => {
  const v = decide(base({ enrolled: false, hands: [hand()] }), T, {});
  assert.equal(v.enabled, false); assert.equal(v.locked, false);
  assert.equal(v.why, "unenrolled"); assert.equal(v.handRole[10], "owner");
});

test("override (?lock=0): never locks", () => {
  const v = decide(base({ lockOverride: true, hands: [hand()] }), T, {});
  assert.equal(v.locked, false); assert.equal(v.handRole[10], "owner");
});

test("identity model not ready: fail closed", () => {
  const v = decide(base({ identityReady: false, faces: [owner()], hands: [hand()] }), T, {});
  assert.equal(v.locked, true); assert.equal(v.why, "identity model not loaded");
  assert.equal(v.handRole[10], "none");
});

test("boot with nobody: locked, owner not in frame", () => {
  const v = decide(base(), T, {});
  assert.equal(v.locked, true); assert.equal(v.why, "owner not in frame");
});

test("a face that does not match: locked, owner not recognized", () => {
  const v = decide(base({ faces: [other()] }), T, {});
  assert.equal(v.locked, true); assert.equal(v.why, "owner not recognized");
});

test("owner live: unlocks after 2 frames, every hand is the owner's when alone", () => {
  const v1 = decide(base({ faces: [owner()], hands: [hand()] }), T, {});
  assert.equal(v1.locked, true);                         // first live frame: still locked
  const v2 = decide(base({ now: 1033, faces: [owner()], hands: [hand()] }), T, v1.state);
  assert.equal(v2.locked, false); assert.equal(v2.why, "owner present");
  assert.equal(v2.ownerId, 1); assert.equal(v2.handRole[10], "owner");
  assert.equal(v2.faceRole[1], "owner");
});

test("photo: matched face with no blink and no head motion is not live", () => {
  const v = run(now => base({ now, faces: [owner({ blinkT: 0, moveT: 0 })] }), 3);
  assert.equal(v.locked, true); assert.equal(v.why, "eyes not live");
});

test("eyes closed: not live", () => {
  const v = run(now => base({ now, faces: [owner({ eyeOpen: 0.1 })] }), 3);
  assert.equal(v.locked, true); assert.equal(v.why, "eyes not live");
});

test("grace: a dropped frame does not lock; past graceMs it does", () => {
  const up = run(now => base({ now, faces: [owner({ blinkT: now, moveT: now })], hands: [hand()] }), 3);
  assert.equal(up.locked, false);
  const t1 = 1000 + 3 * 33;
  const gone1 = decide(base({ now: t1 + 500, hands: [hand()] }), T, up.state);   // owner missing 0.5s
  assert.equal(gone1.locked, false); assert.equal(gone1.handRole[10], "owner");
  const gone2 = decide(base({ now: t1 + T.owner.graceMs + 10, hands: [hand()] }), T, gone1.state);
  assert.equal(gone2.locked, true); assert.equal(gone2.handRole[10], "none");
});

test("owner + far stranger: owner hands work, stranger hands and far hands do not", () => {
  const v = run(now => base({ now,
    faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.52, wy: 0.65 }), hand({ id: 11, wx: 0.9, wy: 0.7 }), hand({ id: 12, wx: 0.05, wy: 0.95 })] }), 3);
  assert.equal(v.locked, false);
  assert.equal(v.faceRole[2], "stranger");
  assert.equal(v.handRole[10], "owner"); assert.equal(v.handRole[11], "none"); assert.equal(v.handRole[12], "none");
  assert.deepEqual(v.guestCandidates, []);
});

test("implausible hand span for the nearest face is not assigned", () => {
  const v = run(now => base({ now, faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.52, wy: 0.65, span: 0.9 })] }), 3);   // span 4.5 face widths
  assert.equal(v.handRole[10], "none");
});

test("close second face is a guest candidate, not yet a guest", () => {
  const v = run(now => base({ now,
    faces: [owner({ blinkT: now, moveT: now }), other({ cx: 0.55, cy: 0.62 })],   // 0.22 away, closeK*w = 0.3
    hands: [hand({ id: 11, wx: 0.56, wy: 0.8 })] }), 3);
  assert.equal(v.faceRole[2], "candidate"); assert.deepEqual(v.guestCandidates, [2]);
  assert.equal(v.handRole[11], "none");
});

test("approved close guest: their hands work; drifting away stops them and revokes after the timeout", () => {
  const approvals = { 2: { approvedT: 1000 } };
  const close = run(now => base({ now, approvals,
    faces: [owner({ blinkT: now, moveT: now }), other({ cx: 0.55, cy: 0.62 })],
    hands: [hand({ id: 11, wx: 0.56, wy: 0.8, span: 0.1 })] }), 3);
  assert.equal(close.faceRole[2], "guest"); assert.equal(close.handRole[11], "guest");
  const t1 = 1000 + 3 * 33;
  const away = decide(base({ now: t1 + 33, approvals,
    faces: [owner({ blinkT: t1 + 33, moveT: t1 + 33 }), other({ cx: 0.9, cy: 0.4 })],
    hands: [hand({ id: 11, wx: 0.9, wy: 0.7, span: 0.1 })] }), T, close.state);
  assert.equal(away.faceRole[2], "guest-away"); assert.equal(away.handRole[11], "none");
  assert.deepEqual(away.revoke, []);
  const later = decide(base({ now: t1 + 33 + T.owner.guestTimeoutMs + 5, approvals,
    faces: [owner({ blinkT: t1 + 33 + T.owner.guestTimeoutMs + 5, moveT: t1 + 33 + T.owner.guestTimeoutMs + 5 }), other({ cx: 0.9, cy: 0.4 })] }), T, away.state);
  assert.deepEqual(later.revoke, [2]);
});

test("approved guest who leaves the frame is revoked after the timeout", () => {
  const approvals = { 2: { approvedT: 1000 } };
  const v1 = run(now => base({ now, approvals, faces: [owner({ blinkT: now, moveT: now })] }), 3);
  assert.deepEqual(v1.revoke, []);
  const v2 = decide(base({ now: 1000 + 99 + T.owner.guestTimeoutMs + 5, approvals,
    faces: [owner({ blinkT: 1000 + 99 + T.owner.guestTimeoutMs + 5, moveT: 1000 + 99 + T.owner.guestTimeoutMs + 5 })] }), T, v1.state);
  assert.deepEqual(v2.revoke, [2]);
});

test("guests never unlock: owner leaves with an approved guest present", () => {
  const approvals = { 2: { approvedT: 1000 } };
  const up = run(now => base({ now, approvals,
    faces: [owner({ blinkT: now, moveT: now }), other({ cx: 0.55, cy: 0.62 })] }), 3);
  assert.equal(up.locked, false);
  const t1 = 1000 + 99;
  const v = decide(base({ now: t1 + T.owner.graceMs + 10, approvals,
    faces: [other({ cx: 0.55, cy: 0.62 })], hands: [hand({ id: 11, wx: 0.56, wy: 0.8 })] }), T, up.state);
  assert.equal(v.locked, true); assert.equal(v.handRole[11], "none");
});

test("the best-scoring matching face is the owner when two faces match", () => {
  const v = run(now => base({ now, faces: [owner({ id: 1, score: 0.45, blinkT: now, moveT: now }),
                                           owner({ id: 2, cx: 0.8, score: 0.2, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.ownerId, 2);
});
