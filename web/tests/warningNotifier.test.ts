import test from "node:test";
import assert from "node:assert/strict";
import { WarningNotifier } from "../src/lib/warningNotifier.ts";
import type { StateMessage } from "../src/types.ts";

const scene = (state: string, countdown: number | null, person = false) => ({
  state, countdown_s: countdown, degraded: [],
  objects: [{ label: "blanket", distance_in: 4, status: "violation", is_person: person }],
}) as unknown as StateMessage;

test("a warning episode notifies once while the countdown runs", () => {
  const n = new WarningNotifier();
  const first = n.next(scene("WARN", 10), false);
  assert.match(first!.body, /blanket is 4 inches.*10 seconds/);
  for (let s = 9; s > 0; s--) assert.equal(n.next(scene("WARN", s), false), null);
});

test("a new episode after the zone clears notifies again", () => {
  const n = new WarningNotifier();
  assert.ok(n.next(scene("WARN", 10), false));
  assert.equal(n.next(scene("CLEAR", null), false), null);
  assert.ok(n.next(scene("WARN", 10), false));
});

test("people and stale data never notify", () => {
  const n = new WarningNotifier();
  assert.equal(n.next(scene("WARN", 10, true), false), null);
  assert.equal(n.next(scene("WARN", 10), true), null);
  assert.equal(n.next(null, false), null);
});
