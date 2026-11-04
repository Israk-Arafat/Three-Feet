import { useSyncExternalStore } from "react";
import type { EventItem, Incident, StateMessage } from "../types";

/** True in the static GitHub Pages build (`npm run build:demo`): the dashboard replays a recording. */
export const DEMO = import.meta.env.VITE_DEMO === "1";
export const DEMO_MESSAGE = "This is a recorded demo. Run Three Feet on your own computer to use this.";

interface Recording {
  start_t: number;
  duration_s: number;
  frame_interval_s: number;
  frames: string[];
  states: { t: number; msg: StateMessage }[];
  events: { t: number; event: Omit<EventItem, "t"> }[];
  incidents: (Omit<Incident, "started_t" | "ended_t"> & { started_t: number; ended_t: number | null })[];
}

interface Snapshot {
  ready: boolean;
  state: StateMessage | null;
  events: EventItem[];
  incidents: Incident[];
  frame: string | null;
}

const TICK_MS = 100;
const base = import.meta.env.BASE_URL;
const listeners = new Set<() => void>();
const EMPTY: Snapshot = { ready: false, state: null, events: [], incidents: [], frame: null };

let snapshot: Snapshot = EMPTY;
let started = false;
let rec: Recording | null = null;
let allEvents: EventItem[] = [];
let stateTimes: number[] = [];
let eventTimes: number[] = [];
let reportedAt: Map<string, number> = new Map();
let startedAt = 0;
let stateIndex = -1;
let frameIndex = -1;
let eventCount = -1;
let incidentCount = -1;

/** Last index whose time is <= t, or -1. `times` must be ascending. */
function lastAtOrBefore(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

function publish(next: Partial<Snapshot>) {
  snapshot = { ...snapshot, ...next };
  listeners.forEach((l) => l());
}

function tick() {
  if (!rec) return;
  const elapsed = ((performance.now() - startedAt) / 1000) % rec.duration_s;
  const si = lastAtOrBefore(stateTimes, elapsed);
  const fi = Math.min(Math.floor(elapsed / rec.frame_interval_s), rec.frames.length - 1);
  const ec = lastAtOrBefore(eventTimes, elapsed) + 1;
  const reported = rec.incidents.filter((i) => (reportedAt.get(i.id) ?? Infinity) <= elapsed);
  if (si === stateIndex && fi === frameIndex && ec === eventCount && reported.length === incidentCount) return;
  const next: Partial<Snapshot> = {};
  if (si !== stateIndex && si >= 0) next.state = { ...rec.states[si].msg, t: rec.start_t + rec.states[si].t };
  if (fi !== frameIndex) next.frame = `${base}demo/${rec.frames[fi]}`;
  if (ec !== eventCount) next.events = allEvents.slice(0, ec);
  if (reported.length !== incidentCount) {
    next.incidents = reported
      .map((i) => ({ ...i, started_t: rec!.start_t + i.started_t, ended_t: i.ended_t === null ? null : rec!.start_t + i.ended_t }))
      .reverse(); // newest first, like the server
  }
  stateIndex = si;
  frameIndex = fi;
  eventCount = ec;
  incidentCount = reported.length;
  publish(next);
}

async function load() {
  const res = await fetch(`${base}demo/recording.json`);
  rec = (await res.json()) as Recording;
  allEvents = rec.events.map((e) => ({ ...e.event, t: rec!.start_t + e.t }) as EventItem);
  stateTimes = rec.states.map((s) => s.t);
  eventTimes = rec.events.map((e) => e.t);
  reportedAt = new Map(
    rec.events
      .filter((e) => e.event.kind === "report" && typeof e.event.data?.incident === "string")
      .map((e) => [e.event.data!.incident as string, e.t]),
  );
  rec.frames.forEach((f) => {
    new Image().src = `${base}demo/${f}`; // warm the cache so the loop never flickers
  });
  startedAt = performance.now();
  publish({ ready: true });
  tick();
  window.setInterval(tick, TICK_MS);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!started && DEMO) {
    started = true;
    load().catch(() => (started = false));
  }
  return () => listeners.delete(listener);
}

const noop = () => () => undefined;

export function useDemoSnapshot(): Snapshot {
  return useSyncExternalStore(DEMO ? subscribe : noop, () => (DEMO ? snapshot : EMPTY));
}

/** The incidents whose report the recording has written so far. */
export function demoIncidents(): Incident[] {
  return snapshot.incidents;
}
