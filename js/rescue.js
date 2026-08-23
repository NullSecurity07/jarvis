// js/rescue.js — THE TWO-SIDED SEARCH (Task 15): pure geometry for a
// face-anchored crop rescue. detectForVideo downscales the ENTIRE frame to
// the model's small input, so a hand occupies very few pixels and the
// dimmer/backlit side loses first. When a frame comes back with fewer than
// 2 hands and a face is tracked, the stage crops a region beside the face
// (sideRegions), runs one extra detect on it, and maps the result back into
// full-frame normalized coordinates (mapLandmarks) before HANDS.assign
// runs — so identity/smoothing/every gesture downstream is unchanged.
// coveredSides decides, from THIS frame's full-frame hands, which side(s)
// already have a hand and need no rescue. Pure numbers; the stage wires it.
// jarvis · SPDX-License-Identifier: AGPL-3.0-or-later

export const TUNE_HANDS2_DEFAULTS = {
  enabled: true, sideW: 1.6, sideH: 2.4, conf: 0.5, dedupe: 0.08,
  maxPerFrame: 1, everyNthFrame: 1,
};

const MIN_W = 0.08;   // a region narrower than this (normalized) is useless

function clamp01(v) { return Math.max(0, Math.min(1, v)); }

function region(rawX0, rawX1, cy, h, sideH) {
  const y0 = clamp01(cy - 0.4 * h), y1 = clamp01(cy - 0.4 * h + sideH * h);
  const x0 = clamp01(rawX0), x1 = clamp01(rawX1);
  const w = x1 - x0, hh = y1 - y0;
  if (w < MIN_W || hh <= 0) return null;
  return { x0, y0, w, h: hh };
}

// face: {cx, cy, w, h} normalized, camera space. -> {left, right}, either
// {x0,y0,w,h} or null (clamped to [0,1], skipped if narrower than 0.08).
export function sideRegions(face, tune) {
  if (!face) return { left: null, right: null };
  const T = tune || TUNE_HANDS2_DEFAULTS;
  const sideW = T.sideW ?? TUNE_HANDS2_DEFAULTS.sideW;
  const sideH = T.sideH ?? TUNE_HANDS2_DEFAULTS.sideH;
  const { cx, cy, w, h } = face;
  const left = region(cx - 0.5 * w - sideW * w, cx - 0.5 * w, cy, h, sideH);
  const right = region(cx + 0.5 * w, cx + 0.5 * w + sideW * w, cy, h, sideH);
  return { left, right };
}

// lms: 21 {x,y,z} points in crop space [0,1]. reg: {x0,y0,w,h} in frame
// space. -> new array in frame-normalized space; z passes through.
export function mapLandmarks(lms, reg) {
  if (!reg || !lms) return [];
  return lms.map(p => ({ x: reg.x0 + p.x * reg.w, y: reg.y0 + p.y * reg.h, z: p.z }));
}

// hands: [{wx, wy}, ...] wrist points from THIS frame's full-frame
// detection. face: {cx, cy, ...}. -> {left, right} booleans: a side already
// has a hand on it and needs no rescue.
export function coveredSides(hands, face) {
  const cov = { left: false, right: false };
  if (!face) return cov;
  for (const hnd of hands || []) {
    if (!hnd || hnd.wx == null) continue;
    if (hnd.wx < face.cx) cov.left = true; else cov.right = true;
  }
  return cov;
}
