// tools/test_identity.mjs — run: node --test tools/test_identity.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { dist, matchScore } from "../js/identity.js";

const v = (fill) => new Float32Array(128).fill(fill);

test("dist: euclidean over 128 dims", () => {
  const a = v(0), b = v(0); b[0] = 3; b[1] = 4;
  assert.equal(dist(a, b), 5);
});

test("matchScore: min distance to any enrolled vector", () => {
  const owner = { vecs: [Array.from(v(0)), Array.from(v(1))] };
  const probe = v(0.9);
  assert.ok(Math.abs(matchScore(probe, owner) - Math.sqrt(128 * 0.01)) < 1e-6);
});

test("matchScore: nothing enrolled = Infinity", () => {
  assert.equal(matchScore(v(0), { vecs: [] }), Infinity);
  assert.equal(matchScore(v(0), null), Infinity);
});

test("matchScore: a same-person pair under 0.5, a different-person pair over it (fixture)", () => {
  // two noisy copies of one face vs an unrelated one
  const me = v(0.1), me2 = v(0.1); me2[5] += 0.2; me2[9] -= 0.2;   // dist 0.28
  const them = v(0.1); for (let i = 0; i < 128; i++) them[i] += (i % 2 ? 0.06 : -0.06);   // dist 0.68
  assert.ok(matchScore(me2, { vecs: [Array.from(me)] }) < 0.5);
  assert.ok(matchScore(them, { vecs: [Array.from(me)] }) > 0.5);
});
