// js/owner.js — THE OWNER LOCK policy. Pure: no DOM, no timers, no globals.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later
//
// decide(input, tune, prev) -> verdict, every frame. The stage hands it the
// faces (with identity scores), the hands (wrist + span), and the approvals
// it keeps; the verdict says whether the board is locked, whose each hand
// is, which faces are guest candidates, and which approvals to revoke.
// verdict.state goes back in as prev next frame (grace, hysteresis, the
// away-timers live there). Coordinates are the camera's normalized video
// space for faces and hands alike; distances are in face widths.

export const TUNE_OWNER_DEFAULTS = {
  enabled: true, matchDist: 0.5, graceMs: 1500, livenessMs: 8000,
  closeK: 1.5, guestTimeoutMs: 10000, handReach: 3.0,
  spanMin: 0.35, spanMax: 2.5, thumbsUpMs: 1000, enrollMs: 3000,
  geomTol: 0.18, driftMax: 0.35, driftEveryMs: 600000, driftCap: 16, propTol: 0.25,
};
export const TUNE_FACE_DEFAULTS = {
  everyNthFrame: 1, idEveryMs: 1500, graceMs: 300, eyeOpenMin: 0.4,
  blinkClose: 0.3, moveDeg: 6, attYaw: 30, attPitch: 25,
};
export const TUNE_POSE_DEFAULTS = { enabled: false, everyNthFrame: 2, wristTol: 0.12, visMin: 0.5 };

// liveness: the owner candidate blinked or moved their head within
// livenessMs, eyes open enough to trust the read. Shared by decide() (the
// owner candidate) and the stage heartbeat (every tracked face) so the two
// never drift apart.
export function isLive(f, tune, now) {
  const F = { ...TUNE_FACE_DEFAULTS, ...((tune && tune.face) || {}) };
  const T = { ...TUNE_OWNER_DEFAULTS, ...((tune && tune.owner) || {}) };
  return f.eyeOpen > F.eyeOpenMin &&
    !!((f.blinkT && now - f.blinkT < T.livenessMs) || (f.moveT && now - f.moveT < T.livenessMs));
}

// THE GEOMETRY GATE (beard-proof corroboration): a small vector of
// bone-structure ratios (js/faces.js computes it, normalised by IPD) that a
// beard trim, new glasses or lighting cannot move. geomDist is a plain mean
// absolute relative difference, b (the owner's enrolled vector) as the
// denominator. geomOk only ever REJECTS a descriptor match the bone
// structure contradicts — every unjudgeable case (no enrolled geometry, no
// current geometry, a non-frontal frame) passes through untouched.
export function geomDist(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]) / Math.max(Math.abs(b[i]), 1e-6);
  return s / a.length;
}
export function geomOk(f, owner, tune) {
  const T = { ...TUNE_OWNER_DEFAULTS, ...((tune && tune.owner) || {}) };
  if (!owner || !owner.geom) return true;      // nothing enrolled to corroborate against
  if (!f || !f.geom) return true;               // this frame's geometry is unreadable
  if (!f.geomOk) return true;                    // not frontal enough to trust the ratios
  if (T.geomTol <= 0) return true;                // 0 = gate disabled
  return geomDist(f.geom, owner.geom) < T.geomTol;
}

// TEMPLATE DRIFT (Task 11 Part B): which vector to drop when the owner's
// drift-appended set goes over the cap. The first `seeds` vecs (the
// enrolment window) are never dropped — a bad drift can never fully
// replace the real you — so the OLDEST non-seed vector goes instead. The
// stage-side writer (stage.html) decides WHEN to append; this decides WHAT
// the resulting array looks like — pure, no DOM/timers.
export function driftAppend(vecs, seeds, vec, cap) {
  const out = vecs.concat([vec]);
  while (out.length > cap) {
    if (seeds < out.length) out.splice(seeds, 1);
    else out.shift();
  }
  return out;
}

// THE GEOMETRY BACKFILL (Task 14 #7): an owner enrolled before the
// geometry gate existed has vecs but no geom, which makes geomOk() a
// permanent no-op for them (see its doc above) — re-enrolling is the only
// other way out. This is the pure "should the stage write one back" check;
// the stage decides WHEN it's looking at the owner's own, unlocked face
// (see stage.html's driftTick) and does the POST — this never touches
// the network, only judges the one frame it's handed.
function validGeom(g) {
  return Array.isArray(g) && g.length === 6 && g.every(Number.isFinite);
}
export function needsGeomBackfill(owner, face, tune) {
  const T = { ...TUNE_OWNER_DEFAULTS, ...((tune && tune.owner) || {}) };
  if (T.geomTol <= 0) return false;                    // the gate is off by configuration
  if (!owner || validGeom(owner.geom)) return false;    // no owner, or already has geometry
  if (!face || !face.seenNow || !face.geomOk || !face.attention) return false;
  if (!Number.isFinite(face.score) || face.score >= T.driftMax) return false;
  return validGeom(face.geom);
}

export function decide(input, tune, prev = {}) {
  const T = { ...TUNE_OWNER_DEFAULTS, ...((tune && tune.owner) || {}) };
  const F = { ...TUNE_FACE_DEFAULTS, ...((tune && tune.face) || {}) };
  const now = input.now;
  const faces = input.faces || [], hands = input.hands || [];
  const approvals = input.approvals || {};
  const st = {
    ownerSeenT: prev.ownerSeenT || 0,
    presentRun: prev.presentRun || 0,
    locked: prev.locked === undefined ? true : prev.locked,   // boot = locked until seen
    lockedSince: prev.lockedSince || 0,
    lastOwner: prev.lastOwner || null,
    away: { ...(prev.away || {}) },
  };
  const faceRole = {}, handRole = {};
  const verdict = (locked, why, ownerId, extra = {}) => ({
    enabled: true, locked, why, ownerId, faceRole, handRole,
    guestCandidates: [], revoke: [], state: st, ...extra,
  });

  // 1. disabled / unenrolled / override: today's board, every hand the owner's
  if (!input.enabled || !input.enrolled || input.lockOverride) {
    faces.forEach(f => { faceRole[f.id] = "unknown"; });
    hands.forEach(h => { handRole[h.id] = "owner"; });
    st.locked = false; st.lockedSince = 0; st.presentRun = 0;
    const why = !input.enabled ? "disabled" : input.lockOverride ? "override" : "unenrolled";
    return { ...verdict(false, why, null), enabled: false };
  }
  // 2. the identity model is not there: fail closed, say why
  if (!input.identityReady) {
    faces.forEach(f => { faceRole[f.id] = "unknown"; });
    hands.forEach(h => { handRole[h.id] = "none"; });
    st.locked = true; if (!st.lockedSince) st.lockedSince = now;
    return verdict(true, "identity model not loaded", null);
  }
  // 3. the owner candidate: best-scoring face under the match bar, LIVE,
  // and corroborated by THE GEOMETRY GATE (Part A) — geometry can only
  // REJECT a descriptor match the bone structure contradicts, never admit
  // one the descriptor rejected (geomOk(...) passes through every
  // unjudgeable case untouched, see js/owner.js's geomOk doc).
  let owner = null;
  for (const f of faces) {
    if (Number.isFinite(f.score) && f.score < T.matchDist && geomOk(f, input.owner, tune) &&
        (!owner || f.score < owner.score)) owner = f;
  }
  const live = !!owner && isLive(owner, tune, now);
  if (live) {
    st.ownerSeenT = now;
    st.lastOwner = { id: owner.id, cx: owner.cx, cy: owner.cy, w: owner.w };
    st.presentRun += 1;
  } else if (!(st.ownerSeenT && now - st.ownerSeenT < T.graceMs)) {
    st.presentRun = 0;
  }
  const present = live || (st.ownerSeenT > 0 && now - st.ownerSeenT < T.graceMs);
  // 4. lock / unlock with hysteresis: 2 live frames to open, the grace to close
  let locked;
  if (!present) locked = true;
  else if (st.locked) locked = !(live && st.presentRun >= 2);
  else locked = false;
  st.locked = locked;
  if (locked) { if (!st.lockedSince) st.lockedSince = now; } else st.lockedSince = 0;
  if (locked) {
    faces.forEach(f => { faceRole[f.id] = (owner && f.id === owner.id) ? "owner" : "unknown"; });
    hands.forEach(h => { handRole[h.id] = "none"; });
    const why = !owner ? (faces.length ? "owner not recognized" : "owner not in frame")
                       : (!live ? "eyes not live" : "unlocking");
    return verdict(true, why, owner ? owner.id : null);
  }
  // 5. unlocked. O = the owner's face (or the last seen one during grace)
  const O = st.lastOwner;   // refreshed on every live frame; non-null whenever unlocked
  faceRole[O.id] = "owner";
  const others = faces.filter(f => f.id !== O.id);
  const guestCandidates = [], revoke = [];
  for (const f of others) {
    const close = Math.hypot(f.cx - O.cx, f.cy - O.cy) <= T.closeK * O.w;
    if (approvals[f.id]) {
      if (close) { faceRole[f.id] = "guest"; delete st.away[f.id]; }
      else {
        faceRole[f.id] = "guest-away";
        if (!st.away[f.id]) st.away[f.id] = now;
        if (now - st.away[f.id] > T.guestTimeoutMs) revoke.push(f.id);
      }
    } else {
      faceRole[f.id] = close ? "candidate" : "stranger";
      if (close) guestCandidates.push(f.id);
    }
  }
  // approved faces that are not in frame at all: revoke after the timeout
  for (const id of Object.keys(approvals)) {
    if (faces.some(f => String(f.id) === String(id))) continue;
    if (!st.away[id]) st.away[id] = now;
    if (now - st.away[id] > T.guestTimeoutMs) revoke.push(Number(id));
  }
  for (const id of Object.keys(st.away)) {   // forget timers for faces that are back or revoked
    if (!approvals[id] && !others.some(f => String(f.id) === id)) delete st.away[id];
  }
  // 6. hands. Alone = every hand is the owner's (today's behaviour, no tests)
  const candidates = faces.some(f => f.id === O.id) ? faces : faces.concat([{ ...O, score: 0 }]);
  // POSE-LINKED HANDS (Task 11 Part C): a body's wrist claims a hand before
  // the face-distance guess ever runs — additive, poses:[] (or the module
  // disabled) falls straight through to today's rule unchanged.
  const P = { ...TUNE_POSE_DEFAULTS, ...((tune && tune.pose) || {}) };
  const poses = input.poses || [];
  function poseForHand(h) {
    const claims = [];
    for (const ps of poses) {
      let d = Infinity;
      // DEGRADE, NEVER GUESS: only a wrist the pose model itself judged
      // usable (visible AND actually inside the frame — see js/pose.js's
      // `ok`) may claim a hand. A body with no usable wrist this frame
      // simply cannot claim anything — it falls through to the
      // face-distance rule below, which is today's shipped behaviour.
      if (ps.wristL && ps.wristL.ok) d = Math.min(d, Math.hypot(h.wx - ps.wristL.x, h.wy - ps.wristL.y));
      if (ps.wristR && ps.wristR.ok) d = Math.min(d, Math.hypot(h.wx - ps.wristR.x, h.wy - ps.wristR.y));
      if (d < P.wristTol) claims.push({ ps, d });
    }
    if (!claims.length) return null;
    // BODY PROPORTION (prop) is a TIE-BREAKER only — it never rejects a
    // hand on its own, it only picks among bodies that already claimed it.
    // It must never GUESS: skip it entirely (fall back to nearest-wrist)
    // unless the owner has an enrolled prop AND every claiming body's prop
    // is a real number — one null prop (a framing that couldn't compute
    // it) is enough to disqualify the whole tie-break, not just that body.
    const ownerProp = input.owner && input.owner.prop;
    if (claims.length > 1 && Number.isFinite(ownerProp) && claims.every(c => c.ps.prop != null)) {
      claims.sort((a, b) => Math.abs(a.ps.prop - ownerProp) - Math.abs(b.ps.prop - ownerProp));
      return claims[0].ps;
    }
    claims.sort((a, b) => a.d - b.d);
    return claims[0].ps;
  }
  function faceForPose(ps) {
    let best = null, bd = Infinity;
    for (const f of candidates) {
      const w = Math.max(f.w, 1e-3);
      const d = ps.nose ? Math.hypot(ps.nose.x - f.cx, ps.nose.y - f.cy) : Infinity;
      if (d < 1.2 * w && d < bd) { bd = d; best = f; }
    }
    return best;
  }
  for (const h of hands) {
    if (others.length === 0) { handRole[h.id] = "owner"; continue; }
    const ps = poseForHand(h);
    if (ps) {
      const f = faceForPose(ps);
      const r = f ? faceRole[f.id] : "none";
      handRole[h.id] = (r === "owner" || r === "guest") ? r : "none";
      continue;
    }
    let best = null, bd = Infinity;
    for (const f of candidates) {
      const w = Math.max(f.w, 1e-3);
      const d = Math.hypot(h.wx - f.cx, h.wy - f.cy) / w;
      const sp = h.span / w;
      if (d < T.handReach && sp >= T.spanMin && sp <= T.spanMax && d < bd) { best = f; bd = d; }
    }
    const r = best ? faceRole[best.id] : "none";
    handRole[h.id] = (r === "owner" || r === "guest") ? r : "none";
  }
  return { ...verdict(false, "owner present", O.id), guestCandidates, revoke };
}
