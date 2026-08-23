// tools/test_rescue.mjs — run: node --test tools/test_rescue.mjs
// THE TWO-SIDED SEARCH (Task 15): sideRegions / mapLandmarks / coveredSides,
// pure geometry, no MediaPipe involved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { sideRegions, mapLandmarks, coveredSides, TUNE_HANDS2_DEFAULTS } from "../js/rescue.js";

const T = { ...TUNE_HANDS2_DEFAULTS };

test("sideRegions: a centred face gets two valid regions inside [0,1]", () => {
  const face = { cx: 0.5, cy: 0.5, w: 0.15, h: 0.15 };
  const { left, right } = sideRegions(face, T);
  assert.ok(left && right, "both sides should be present for a centred face");
  for (const r of [left, right]) {
    assert.ok(r.x0 >= 0 && r.x0 <= 1 && r.y0 >= 0 && r.y0 <= 1);
    assert.ok(r.x0 + r.w >= 0 && r.x0 + r.w <= 1);
    assert.ok(r.y0 + r.h >= 0 && r.y0 + r.h <= 1);
    assert.ok(r.w >= 0.08);
  }
  assert.ok(left.x0 < right.x0, "left region sits left of the right region");
});

test("sideRegions: a raw region reaching past x=0 clamps to the frame edge, not null", () => {
  // face near the left edge but wide enough that the clamped region still
  // clears the 0.08 minimum width
  const face = { cx: 0.3, cy: 0.5, w: 0.2, h: 0.2 };
  const { left } = sideRegions(face, T);
  assert.ok(left, "clamped-but-usable region must survive");
  assert.equal(left.x0, 0, "x0 clamps to the frame edge");
  assert.ok(left.w >= 0.08);
});

test("sideRegions: a region narrower than 0.08 after clamping is null", () => {
  // face hugging the left edge: the whole left region clamps to zero width
  const face = { cx: 0.02, cy: 0.5, w: 0.1, h: 0.1 };
  const { left, right } = sideRegions(face, T);
  assert.equal(left, null, "left region collapses under the frame edge");
  assert.ok(right, "right region, far from any edge, stays valid");
});

test("sideRegions: no face -> both sides null", () => {
  const { left, right } = sideRegions(null, T);
  assert.equal(left, null);
  assert.equal(right, null);
});

test("mapLandmarks: round-trips a known point from crop space into frame space", () => {
  const reg = { x0: 0.1, y0: 0.2, w: 0.3, h: 0.4 };
  const crop = [{ x: 0.5, y: 0.25, z: -0.01 }];
  const mapped = mapLandmarks(crop, reg);
  assert.equal(mapped.length, 1);
  assert.ok(Math.abs(mapped[0].x - (0.1 + 0.5 * 0.3)) < 1e-9);
  assert.ok(Math.abs(mapped[0].y - (0.2 + 0.25 * 0.4)) < 1e-9);
  assert.equal(mapped[0].z, -0.01, "z passes through unchanged");
});

test("mapLandmarks: null region -> empty array", () => {
  assert.deepEqual(mapLandmarks([{ x: 0.5, y: 0.5, z: 0 }], null), []);
});

test("coveredSides: a hand's wrist x left of face cx covers the left side", () => {
  const face = { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 };
  const hands = [{ wx: 0.2, wy: 0.5 }];
  assert.deepEqual(coveredSides(hands, face), { left: true, right: false });
});

test("coveredSides: a hand's wrist x right of face cx covers the right side", () => {
  const face = { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 };
  const hands = [{ wx: 0.8, wy: 0.5 }];
  assert.deepEqual(coveredSides(hands, face), { left: false, right: true });
});

test("coveredSides: two hands, one per side, cover both", () => {
  const face = { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 };
  const hands = [{ wx: 0.2, wy: 0.5 }, { wx: 0.9, wy: 0.5 }];
  assert.deepEqual(coveredSides(hands, face), { left: true, right: true });
});

test("coveredSides: no hands -> neither side covered", () => {
  const face = { cx: 0.5, cy: 0.5, w: 0.1, h: 0.1 };
  assert.deepEqual(coveredSides([], face), { left: false, right: false });
});
