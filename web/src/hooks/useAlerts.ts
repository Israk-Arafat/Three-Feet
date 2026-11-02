import { useCallback, useEffect, useRef, useState } from "react";
import { capitalize, humanFlag, spokenInches } from "../lib/format";
import type { SafetyState, StateMessage } from "../types";

type Pattern = "chirp" | "warn" | "confirm" | "siren" | "degraded";

class Beeper {
  private ctx: AudioContext | null = null;

  unlock() {
    this.ctx ??= new AudioContext();
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  private tone(freq: number, start: number, dur: number, type: OscillatorType = "sine", gain = 0.18) {
    if (!this.ctx) return;
    const osc = this.ctx.createOscillator();
    const amp = this.ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    const t0 = this.ctx.currentTime + start;
    amp.gain.setValueAtTime(0, t0);
    amp.gain.linearRampToValueAtTime(gain, t0 + 0.01);
    amp.gain.linearRampToValueAtTime(0, t0 + dur);
    osc.connect(amp).connect(this.ctx.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  play(p: Pattern) {
    switch (p) {
      case "chirp":
        this.tone(880, 0, 0.12);
        break;
      case "warn":
        [0, 0.22, 0.44].forEach((s) => this.tone(1046, s, 0.15, "square", 0.12));
        break;
      case "confirm":
        this.tone(660, 0, 0.18);
        this.tone(990, 0.2, 0.3);
        break;
      case "siren":
        for (let i = 0; i < 4; i++) {
          this.tone(1200, i * 0.5, 0.25, "sawtooth", 0.14);
          this.tone(800, i * 0.5 + 0.25, 0.25, "sawtooth", 0.14);
        }
        break;
      case "degraded":
        this.tone(330, 0, 0.35, "triangle");
        this.tone(262, 0.4, 0.45, "triangle");
        break;
    }
  }
}

function say(text: string) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  window.speechSynthesis.speak(u);
}

function notify(title: string, body: string) {
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    new Notification(title, { body, tag: "three-feet" });
  }
}

/** Sound, speech, and notifications on state transitions. Browsers require a click before audio plays. */
export function useAlerts(state: StateMessage | null, stale: boolean) {
  const [enabled, setEnabled] = useState(false);
  const beeper = useRef(new Beeper());
  const prev = useRef<{ state: SafetyState | null; degraded: string[]; stale: boolean }>({
    state: null,
    degraded: [],
    stale: false,
  });

  const enable = useCallback(() => {
    beeper.current.unlock();
    if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission();
    setEnabled(true);
    say("Alerts on.");
  }, []);

  const disable = useCallback(() => {
    window.speechSynthesis?.cancel();
    setEnabled(false);
  }, []);

  // Transitions
  useEffect(() => {
    if (!state) return;
    const p = prev.current;
    const cur = state.state;
    const newFlags = state.degraded.filter((f) => !p.degraded.includes(f) && f !== "API_DOWN");
    prev.current = { state: cur, degraded: state.degraded, stale };
    if (!enabled || p.state === null) return;

    if (stale && !p.stale) {
      beeper.current.play("degraded");
      say("Connection to Three Feet lost. Status unknown.");
      return;
    }
    if (cur !== p.state) {
      const worst = state.objects[0];
      if (cur === "WATCH") beeper.current.play("chirp");
      if (cur === "WARN" && worst) {
        beeper.current.play("warn");
        const secs = state.countdown_s !== null ? Math.ceil(state.countdown_s) : null;
        const msg = `${capitalize(worst.label)} is ${spokenInches(worst.distance_in)} from the heater.${
          secs ? ` Turning off in ${secs} seconds.` : ""
        }`;
        say(msg);
        notify("Three Feet: object too close", msg);
      }
      if (cur === "OFF_CONFIRMED") {
        beeper.current.play("confirm");
        const n = [state.signals.power_off, state.signals.indicator_off].filter(Boolean).length;
        const msg = n === 2 ? "Heater is off. Confirmed by power and light." : "Heater is off. Confirmed by power.";
        say(msg);
        notify("Three Feet: heater off", msg);
      }
      if (cur === "OFF_UNCONFIRMED") {
        beeper.current.play("siren");
        say("Shutoff not confirmed. Check the heater now.");
        notify("Three Feet: CHECK THE HEATER", "Shutoff could not be confirmed.");
      }
    }
    if (newFlags.length) {
      beeper.current.play("degraded");
      say(`${capitalize(humanFlag(newFlags[0]))}. Status unknown.`);
      notify("Three Feet: status unknown", newFlags.map(humanFlag).join(", "));
    }
  }, [state, stale, enabled]);

  // Repeating patterns while the situation persists
  const situation = stale ? "stale" : state?.state;
  useEffect(() => {
    if (!enabled) return;
    if (situation !== "WARN" && situation !== "OFF_UNCONFIRMED") return;
    const id = window.setInterval(
      () => beeper.current.play(situation === "WARN" ? "warn" : "siren"),
      situation === "WARN" ? 2500 : 2200,
    );
    return () => window.clearInterval(id);
  }, [situation, enabled]);

  return { enabled, enable, disable };
}
