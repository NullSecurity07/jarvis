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
};
export const TUNE_FACE_DEFAULTS = {
  everyNthFrame: 1, idEveryMs: 1500, graceMs: 300, eyeOpenMin: 0.4,
  blinkClose: 0.3, moveDeg: 6, attYaw: 30, attPitch: 25,
};

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
  // 3. the owner candidate: best-scoring face under the match bar, and LIVE
  let owner = null;
  for (const f of faces) {
    if (Number.isFinite(f.score) && f.score < T.matchDist && (!owner || f.score < owner.score)) owner = f;
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
  for (const h of hands) {
    if (others.length === 0) { handRole[h.id] = "owner"; continue; }
    let best = null, bd = Infinity;
    const candidates = faces.some(f => f.id === O.id) ? faces : faces.concat([{ ...O, score: 0 }]);
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
