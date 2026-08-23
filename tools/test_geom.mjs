// tools/test_geom.mjs — run: node --test tools/test_geom.mjs
// THE GEOMETRY GATE (Task 11 Part A): geomDist / geomOk, pure math.
import { test } from "node:test";
import assert from "node:assert/strict";
import { geomDist, geomOk, TUNE_OWNER_DEFAULTS } from "../js/owner.js";

const T = { owner: { ...TUNE_OWNER_DEFAULTS } };
const V = [0.42, 0.55, 1.1, 0.9, 0.3, 1.6];   // a plausible 6-ratio geom vector

test("geomDist: identical vectors -> 0", () => {
  assert.equal(geomDist(V, V), 0);
});

test("geomDist: a uniform 10% shift -> 0.1", () => {
  const shifted = V.map(x => x * 1.1);
  assert.ok(Math.abs(geomDist(shifted, V) - 0.1) < 1e-9);
});

test("geomOk: true when the owner has no enrolled geometry", () => {
  const f = { geom: V, geomOk: true };
  assert.equal(geomOk(f, { geom: null }, T), true);
  assert.equal(geomOk(f, {}, T), true);
});

test("geomOk: true when the face's geom is null (unjudgeable never blocks)", () => {
  const f = { geom: null, geomOk: true };
  assert.equal(geomOk(f, { geom: V }, T), true);
});

test("geomOk: true when the face is not frontal (f.geomOk false) even if the vector mismatches", () => {
  const f = { geom: V.map(x => x * 3), geomOk: false };
  assert.equal(geomOk(f, { geom: V }, T), true);
});

test("geomOk: false when the geometry distance exceeds geomTol", () => {
  const f = { geom: V.map(x => x * 1.5), geomOk: true };   // way off (50% shift)
  assert.equal(geomOk(f, { geom: V }, T), false);
});

test("geomOk: true when the geometry distance is under geomTol", () => {
  const f = { geom: V.map(x => x * 1.02), geomOk: true };   // 2% shift, tol default 0.18
  assert.equal(geomOk(f, { geom: V }, T), true);
});

test("geomOk: geomTol 0 disables the gate entirely", () => {
  const f = { geom: V.map(x => x * 5), geomOk: true };   // wildly off
  const t0 = { owner: { ...TUNE_OWNER_DEFAULTS, geomTol: 0 } };
  assert.equal(geomOk(f, { geom: V }, t0), true);
});
