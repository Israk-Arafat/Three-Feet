import type { StateMessage } from "../types";

export type AlertPattern = "warn" | "siren" | "degraded";
export interface Announcement {
  key: string;
  message: string;
  title: string;
  priority: number;
  pattern?: AlertPattern;
}

interface Condition extends Announcement {
  delay: number;
  cooldown: number;
}
interface Episode {
  since: number;
  absentSince: number | null;
  announced: boolean;
}

const RECOVERY_MS = 15_000;
const FAULT_DELAY_MS = 5_000;
const FAULT_MESSAGES: Record<string, string> = {
  CAMERA_LOST: "Camera feed lost. Status unknown.",
  CALIBRATION_DRIFT: "Camera moved. Check the heater calibration.",
  PLUG_UNREACHABLE: "Smart plug unreachable. Check its connection.",
};

/** Scene analysis is expected work, not an outage that needs a voice announcement. */
export function isRoutineAnalysis(state: StateMessage): boolean {
  if (state.api.last_error) return false;
  const reason = state.api.health_reason ?? "";
  return /^Waiting for the first successful object detection/i.test(reason)
    || /^Scene changed or local tracking uncertain/i.test(reason)
    || (state.objects.some((o) => o.status === "pending")
      && /^Object detections are stale; waiting for a fresh result/i.test(reason));
}

/** Stateful notification policy; camera/SAM updates never directly drive speech. */
export class AlertPolicy {
  private episodes = new Map<string, Episode>();
  private spokenAt = new Map<string, number>();
  private previousState: string | null = null;
  private offSequence = 0;

  next(state: StateMessage, stale: boolean, now: number, minimumPriority = 0): Announcement | null {
    if (state.state === "OFF_REQUESTED" && this.previousState !== "OFF_REQUESTED") this.offSequence++;
    this.previousState = state.state;
    const conditions: Condition[] = [];
    if (stale) {
      conditions.push({ key: "connection", message: "Connection to Three Feet lost. Status unknown.",
        title: "Three Feet: connection lost", priority: 1, delay: 3000, cooldown: 60_000, pattern: "degraded" });
    } else {
      const worst = state.objects[0];
      if (state.state === "WARN" && state.countdown_s !== null && !state.degraded.length
          && worst?.status === "violation" && !worst.is_person) {
        const seconds = Math.ceil(state.countdown_s);
        conditions.push({ key: `warning:${worst.label.trim().toLowerCase()}`,
          message: `${worst.label} is ${Math.max(0, Math.round(worst.distance_in))} inches from the heater. Turning off in ${seconds} seconds.`,
          title: "Three Feet: object too close", priority: 3, pattern: "warn", delay: 0, cooldown: 30_000 });
      }
      if (state.state === "OFF_REQUESTED") {
        if (!state.off_pending && state.off_error) {
          conditions.push({ key: `off-error:${this.offSequence}`, message: "Power off request failed. Check the heater now.",
            title: "Three Feet: power off request failed", priority: 4, pattern: "siren", delay: 0, cooldown: 0 });
        } else if (!state.off_pending) {
          conditions.push({ key: `off:${this.offSequence}`, message: "Power off requested.",
            title: "Three Feet: power off requested", priority: 2, delay: 0, cooldown: 0 });
        }
      }
      for (const flag of state.degraded) {
        if (flag === "API_DOWN" && isRoutineAnalysis(state)) continue;
        const message = flag === "API_DOWN"
          ? /budget/i.test(state.api.health_reason ?? "")
            ? "Model monitoring is paused because the API budget was reached."
            : "Object detection is unavailable. Check the connection or API status."
          : FAULT_MESSAGES[flag] ?? "Monitoring is unavailable. Check the dashboard.";
        conditions.push({ key: `fault:${flag}`, message, title: "Three Feet: monitoring needs attention",
          priority: 1, pattern: "degraded", delay: FAULT_DELAY_MS, cooldown: 60_000 });
      }
    }

    const active = new Set(conditions.map((condition) => condition.key));
    for (const [key, episode] of this.episodes) {
      if (!active.has(key)) {
        episode.absentSince ??= now;
        if (now - episode.absentSince >= RECOVERY_MS) this.episodes.delete(key);
      }
    }
    for (const condition of conditions) {
      const episode = this.episodes.get(condition.key);
      if (!episode) this.episodes.set(condition.key, { since: now, absentSince: null, announced: false });
      else if (episode.absentSince !== null) {
        // A healthy blip restarts debounce but does not re-arm an already announced fault.
        episode.since = now;
        episode.absentSince = null;
      }
    }
    for (const condition of conditions.sort((a, b) => b.priority - a.priority)) {
      const episode = this.episodes.get(condition.key)!;
      if (condition.priority < minimumPriority || episode.announced || now - episode.since < condition.delay) continue;
      if (now - (this.spokenAt.get(condition.key) ?? -Infinity) < condition.cooldown) continue;
      episode.announced = true;
      this.spokenAt.set(condition.key, now);
      return condition;
    }
    return null;
  }
}
