import type { StateMessage } from "../types";

export interface WarningNotice {
  title: string;
  body: string;
}

/** Yields one notice per warning episode, so a countdown never repeats it. */
export class WarningNotifier {
  private active = false;

  next(state: StateMessage | null, stale: boolean): WarningNotice | null {
    const worst = state?.objects[0];
    const warning = !!state && !stale && state.state === "WARN" && state.countdown_s !== null
      && !!worst && worst.status === "violation" && !worst.is_person;
    if (!warning) {
      if (state && !stale) this.active = false;
      return null;
    }
    if (this.active) return null;
    this.active = true;
    const seconds = Math.max(1, Math.ceil(state.countdown_s!));
    return {
      title: "Three Feet: object too close to the heater",
      body: `${worst.label} is ${Math.max(0, Math.round(worst.distance_in))} inches from the heater. Turning off in ${seconds} seconds.`,
    };
  }
}
