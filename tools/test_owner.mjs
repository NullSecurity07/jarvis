// tools/test_owner.mjs — run: node --test tools/test_owner.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, isLive, driftAppend, needsGeomBackfill, TUNE_OWNER_DEFAULTS, TUNE_FACE_DEFAULTS } from "../js/owner.js";

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

test("isLive matches what decide() treats as live", () => {
  assert.equal(isLive(owner({ blinkT: 0, moveT: 0 }), T, 1000), false);
  assert.equal(isLive(owner({ blinkT: 950, moveT: 0 }), T, 1000), true);
});

test("the best-scoring matching face is the owner when two faces match", () => {
  const v = run(now => base({ now, faces: [owner({ id: 1, score: 0.45, blinkT: now, moveT: now }),
                                           owner({ id: 2, cx: 0.8, score: 0.2, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.ownerId, 2);
});

// THE GEOMETRY GATE (Task 11 Part A): a face under the descriptor bar is
// only the owner candidate when bone-structure geometry doesn't contradict
// it. GEOM is the enrolled owner's ratio vector; MISMATCH is far enough
// off (>geomTol default 0.18) to trip the gate, MATCH is a close read.
const GEOM = [0.42, 0.55, 1.1, 0.9, 0.3, 1.6];
const MATCH = GEOM.map(x => x * 1.02);      // 2% off — well under geomTol
const MISMATCH = GEOM.map(x => x * 1.5);    // 50% off — trips the gate

test("descriptor match but geometry mismatch: NOT the owner, locked", () => {
  const v = decide(base({ owner: { geom: GEOM },
    faces: [owner({ geom: MISMATCH, geomOk: true })] }), T, {});
  assert.equal(v.locked, true); assert.equal(v.why, "owner not recognized");
  assert.equal(v.ownerId, null);
});

test("descriptor match and geometry match: IS the owner, unlocks", () => {
  const v = run(now => base({ owner: { geom: GEOM }, now,
    faces: [owner({ geom: MATCH, geomOk: true, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.locked, false); assert.equal(v.ownerId, 1);
});

test("f.geom null (unreadable this frame): judged on descriptor alone", () => {
  const v = run(now => base({ owner: { geom: GEOM }, now,
    faces: [owner({ geom: null, geomOk: true, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.locked, false); assert.equal(v.ownerId, 1);
});

test("non-frontal face (f.geomOk false): unjudgeable never blocks", () => {
  const v = run(now => base({ owner: { geom: GEOM }, now,
    faces: [owner({ geom: MISMATCH, geomOk: false, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.locked, false); assert.equal(v.ownerId, 1);
});

test("no enrolled geometry on file: descriptor alone decides, gate is a no-op", () => {
  const v = run(now => base({ now,
    faces: [owner({ geom: MISMATCH, geomOk: true, blinkT: now, moveT: now })] }), 3);
  assert.equal(v.locked, false); assert.equal(v.ownerId, 1);
});

// POSE-LINKED HANDS (Task 11 Part C): a body's wrist claims a hand, that
// body maps to a face by nose proximity, and that face's role decides the
// hand's role — a skeleton fact, ahead of the face-distance fallback.
const pose = (o = {}) => ({ id: 20, nose: { x: 0.5, y: 0.4 },
  wristL: { x: 0.45, y: 0.7, vis: 0.9 }, wristR: { x: 0.55, y: 0.7, vis: 0.9 },
  shoulderL: { x: 0.4, y: 0.45 }, shoulderR: { x: 0.6, y: 0.45 },
  shoulderW: 0.2, headW: 0.12, torso: 0.3, prop: 1.6, ...o });

test("pose-linked: a hand near the owner's wrist is the owner's even when a stranger's face is nearer to it", () => {
  // the hand sits right next to the stranger (id 2, cx 0.9) — the old
  // face-distance rule would pick the stranger (closer) and reject the
  // hand (stranger isn't owner/guest). The pose says otherwise: its wrist
  // is exactly at the hand, and its nose is at the OWNER's face.
  const v = run(now => base({ now,
    faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.85, wy: 0.65 })],
    poses: [pose({ wristR: { x: 0.85, y: 0.65, vis: 0.9 } })] }), 3);
  assert.equal(v.locked, false);
  assert.equal(v.faceRole[2], "stranger");           // the face rule alone would reject this hand
  assert.equal(v.handRole[10], "owner");              // the pose corrects it
});

test("pose-linked: a hand no pose claims falls back to today's face-distance rule", () => {
  const v = run(now => base({ now,
    faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.52, wy: 0.65 }), hand({ id: 11, wx: 0.9, wy: 0.7 })],
    poses: [pose({ nose: { x: 0.5, y: 0.99 }, wristL: { x: 0.01, y: 0.01, vis: 0.9 },
                    wristR: { x: 0.02, y: 0.02, vis: 0.9 } })] }), 3);   // this pose claims nothing
  assert.equal(v.handRole[10], "owner"); assert.equal(v.handRole[11], "none");
});

test("pose-linked: poses: [] behaves exactly like today (no poses module / disabled)", () => {
  const v = run(now => base({ now,
    faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.52, wy: 0.65 }), hand({ id: 11, wx: 0.9, wy: 0.7 })],
    poses: [] }), 3);
  assert.equal(v.handRole[10], "owner"); assert.equal(v.handRole[11], "none");
});

test("pose-linked: prop is a tie-breaker only, when two bodies both claim a hand", () => {
  // owner's enrolled prop is 1.6. The hand sits right next to the stranger
  // (so the face rule, and a distance-only pose pick, would both lose it).
  // Two bodies both have a wrist right at the hand — body A (id 21, nose at
  // the stranger's face) has a mismatched prop, body B (id 22, nose at the
  // owner's face) matches. B should win the hand on prop alone.
  const v = run(now => base({ now,
    owner: { prop: 1.6 },
    faces: [owner({ blinkT: now, moveT: now }), other()],
    hands: [hand({ id: 10, wx: 0.85, wy: 0.65 })],
    poses: [
      pose({ id: 21, nose: { x: 0.9, y: 0.4 }, prop: 2.4, wristR: { x: 0.85, y: 0.65, vis: 0.9 } }),
      pose({ id: 22, nose: { x: 0.5, y: 0.4 }, prop: 1.62, wristR: { x: 0.85, y: 0.65, vis: 0.9 } }),
    ] }), 3);
  assert.equal(v.faceRole[2], "stranger");   // the face rule alone would reject this hand
  assert.equal(v.handRole[10], "owner");
});

// TEMPLATE DRIFT (Task 11 Part B): driftAppend(vecs, seeds, vec, cap) — the
// pure "which vector do I drop" decision. The first `seeds` vecs are the
// enrolment window and are never dropped; over the cap, the OLDEST
// non-seed vector goes so a bad drift can never fully replace the real you.
test("driftAppend: under the cap, just appends", () => {
  const vecs = ["s0", "s1", "s2", "d0"];
  assert.deepEqual(driftAppend(vecs, 3, "d1", 6), ["s0", "s1", "s2", "d0", "d1"]);
});

test("driftAppend: at the cap, drops the OLDEST non-seed vector, keeps seeds", () => {
  const vecs = ["s0", "s1", "s2", "d0", "d1", "d2"];   // 3 seeds + 3 drift vecs, cap 6
  assert.deepEqual(driftAppend(vecs, 3, "d3", 6), ["s0", "s1", "s2", "d1", "d2", "d3"]);
});

test("driftAppend: seeds are never dropped even after many drifts", () => {
  let vecs = ["s0", "s1", "s2"];
  for (let i = 0; i < 20; i++) vecs = driftAppend(vecs, 3, "d" + i, 6);
  assert.deepEqual(vecs.slice(0, 3), ["s0", "s1", "s2"]);
  assert.equal(vecs.length, 6);
  assert.deepEqual(vecs.slice(3), ["d17", "d18", "d19"]);
});

// THE GEOMETRY BACKFILL (Task 14 #7): needsGeomBackfill(owner, face, tune)
// is the pure "should the stage write geom back" check — an owner enrolled
// before the geometry gate existed has vecs but no geom, which makes
// geomOk() a permanent no-op for them. True only in the one good case.
const geomFace = (o = {}) => ({ seenNow: true, geomOk: true, attention: true, score: 0.1, geom: GEOM, ...o });

test("needsGeomBackfill: false when the owner already has geom", () => {
  assert.equal(needsGeomBackfill({ geom: GEOM, vecs: [] }, geomFace(), T), false);
});

test("needsGeomBackfill: false when the face isn't frontal/confident", () => {
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace({ geomOk: false }), T), false);
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace({ attention: false }), T), false);
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace({ seenNow: false }), T), false);
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace({ score: T.owner.driftMax }), T), false);
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace({ geom: null }), T), false);
});

test("needsGeomBackfill: false when geomTol is 0 (the gate is off by configuration)", () => {
  const t = { owner: { ...TUNE_OWNER_DEFAULTS, geomTol: 0 }, face: { ...TUNE_FACE_DEFAULTS } };
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace(), t), false);
});

test("needsGeomBackfill: true — owner has no geom, face is frontal, confident, geom-valid", () => {
  assert.equal(needsGeomBackfill({ vecs: [] }, geomFace(), T), true);
});

test("needsGeomBackfill: a malformed owner.geom (wrong length) is not \"already has geometry\" — backfill still proceeds", () => {
  assert.equal(needsGeomBackfill({ vecs: [], geom: [1, 2, 3] }, geomFace(), T), true);
});
