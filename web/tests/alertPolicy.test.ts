import test from "node:test";
import assert from "node:assert/strict";
import { AlertPolicy } from "../src/lib/alertPolicy.ts";
import type { StateMessage } from "../src/types.ts";

function scene(overrides: Partial<StateMessage> = {}): StateMessage {
  return {
    state: "CLEAR", countdown_s: null, degraded: [], objects: [], off_pending: false, off_error: null,
    api: { ok: true, health_reason: null, last_error: null },
    ...overrides,
  } as StateMessage;
}
function fault(reason = "ConnectionError: API unavailable", error: string | null = reason) {
  return scene({ degraded: ["API_DOWN"], api: { ok: false, health_reason: reason, last_error: error } as StateMessage["api"] });
}
function warning(track = "first") {
  return scene({ state: "WARN", countdown_s: 10, objects: [{
    track_id: track, label: "blanket", distance_in: 4, status: "violation", is_person: false,
  }] as StateMessage["objects"] });
}

test("frequent routine SAM transitions stay silent for five minutes", () => {
  const policy = new AlertPolicy();
  for (let t = 0; t < 300_000; t += 250) {
    const state = t % 6000 < 3000
      ? fault("Scene changed or local tracking uncertain; waiting for model verification", null)
      : scene();
    assert.equal(policy.next(state, false, t), null);
  }
});

test("waiting for an initial response or an unidentified object is silent", () => {
  const policy = new AlertPolicy();
  const startup = fault("Waiting for the first successful object detection", null);
  const pending = fault("Object detections are stale; waiting for a fresh result", null);
  pending.objects = [{ status: "pending" }] as StateMessage["objects"];
  for (let t = 0; t < 60_000; t += 250) {
    assert.equal(policy.next(t < 30_000 ? startup : pending, false, t), null);
  }
});

test("brief outages are debounced; a real continuing outage is announced once", () => {
  const policy = new AlertPolicy();
  assert.equal(policy.next(fault(), false, 0), null);
  assert.equal(policy.next(scene(), false, 4000), null);
  assert.equal(policy.next(fault(), false, 6000), null);
  assert.equal(policy.next(fault(), false, 10_999), null);
  assert.match(policy.next(fault(), false, 11_000)!.message, /unavailable/);
  for (let t = 11_250; t < 300_000; t += 250) assert.equal(policy.next(fault(), false, t), null);
});

test("healthy blips do not re-arm a spoken outage", () => {
  const policy = new AlertPolicy();
  policy.next(fault(), false, 0);
  assert.ok(policy.next(fault(), false, 5000));
  for (let t = 6000; t < 300_000; t += 1000) {
    assert.equal(policy.next(t % 7000 === 0 ? scene() : fault(), false, t), null);
  }
});

test("a new sustained outage is announced after stable recovery", () => {
  const policy = new AlertPolicy();
  policy.next(fault(), false, 0);
  assert.ok(policy.next(fault(), false, 5000));
  policy.next(scene(), false, 6000);
  policy.next(scene(), false, 21_000);
  assert.equal(policy.next(fault(), false, 70_000), null);
  assert.ok(policy.next(fault(), false, 75_000));
});

test("a warning is immediate, with no repeat when track IDs or state flicker", () => {
  const policy = new AlertPolicy();
  assert.equal(policy.next(warning(), false, 0)!.priority, 3);
  for (let t = 250; t < 60_000; t += 250) {
    assert.equal(policy.next(t % 2000 === 0 ? scene() : warning(String(t)), false, t), null);
  }
});

test("noncritical speech waits; a failed OFF request can interrupt a warning", () => {
  const policy = new AlertPolicy();
  const camera = scene({ degraded: ["CAMERA_LOST"] });
  policy.next(camera, false, 0);
  assert.equal(policy.next(camera, false, 5000, 4), null);
  assert.equal(policy.next(camera, false, 6000)!.priority, 1);
  const error = scene({ state: "OFF_REQUESTED", off_error: "command failed" });
  assert.equal(policy.next(error, false, 6100, 4)!.priority, 4);
  assert.equal(policy.next(error, false, 6200), null);
});

test("stale state cannot announce a cached warning or shutdown", () => {
  const policy = new AlertPolicy();
  assert.equal(policy.next(warning(), true, 0), null);
  assert.equal(policy.next(warning(), true, 3000)!.key, "connection");
  assert.equal(policy.next(scene({ state: "OFF_REQUESTED" }), true, 4000), null);
});
