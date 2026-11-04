import { useCallback, useEffect, useRef, useState } from "react";
import { AlertPolicy, type AlertPattern } from "../lib/alertPolicy";
import { WarningNotifier } from "../lib/warningNotifier";
import type { StateMessage } from "../types";


class Beeper {
  private ctx: AudioContext | null = null;

  unlock() {
    this.ctx ??= new AudioContext();
    if (this.ctx.state === "suspended") void this.ctx.resume();
  }

  private tone(freq: number, start: number, dur: number, type: OscillatorType = "sine", gain = 0.07) {
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

  play(p: AlertPattern) {
    switch (p) {
      case "warn":
        [0, 0.22].forEach((s) => this.tone(1046, s, 0.12, "sine", 0.07));
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

function say(text: string, interrupt = false) {
  if (!("speechSynthesis" in window)) return;
  if (interrupt) window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  u.volume = 0.7;
  window.speechSynthesis.speak(u);
}

function notify(title: string, body: string) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  const note = new Notification(title, { body, tag: "three-feet-warning", requireInteraction: true });
  note.onclick = () => {
    window.focus();
    note.close();
  };
}

const STORE_KEY = "three-feet.alerts";

const remembered = () => {
  try {
    return localStorage.getItem(STORE_KEY) === "on";
  } catch {
    return false;
  }
};

const remember = (on: boolean) => {
  try {
    localStorage.setItem(STORE_KEY, on ? "on" : "off");
  } catch {
    /* private mode */
  }
};

/**
 * Sound, speech, and notifications on state transitions. Browsers require a click before audio plays, so a
 * reload leaves alerts `paused` until the first click or key press anywhere on the page re-arms them.
 */
export function useAlerts(state: StateMessage | null, stale: boolean) {
  const [enabled, setEnabled] = useState(false);
  const [paused, setPaused] = useState(remembered);
  const beeper = useRef(new Beeper());
  const policy = useRef(new AlertPolicy());
  const notifier = useRef(new WarningNotifier());
  const voicePriority = useRef(0);
  const latest = useRef({ state, stale });
  latest.current = { state, stale };

  const enable = useCallback(() => {
    beeper.current.unlock();
    if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission();
    remember(true);
    setPaused(false);
    setEnabled(true);
    // Enabling alerts should not queue a greeting ahead of an actual hazard.
  }, []);

  const disable = useCallback(() => {
    window.speechSynthesis?.cancel();
    voicePriority.current = 0;
    remember(false);
    setPaused(false);
    setEnabled(false);
  }, []);

  useEffect(() => {
    if (!paused) return;
    const resume = (e: Event) => {
      // The alerts button handles its own click; re-arming on its pointerdown would flip it straight back off.
      if (e.target instanceof Element && e.target.closest("[data-alerts-toggle]")) return;
      beeper.current.unlock();
      setPaused(false);
      setEnabled(true);
    };
    window.addEventListener("pointerdown", resume);
    window.addEventListener("keydown", resume);
    return () => {
      window.removeEventListener("pointerdown", resume);
      window.removeEventListener("keydown", resume);
    };
  }, [paused]);

  // Notifications need no audio unlock, so they stay armed after a reload while alerts are `paused`.
  useEffect(() => {
    if (!enabled && !paused) return;
    const check = () => {
      const { state: current, stale: isStale } = latest.current;
      const notice = notifier.current.next(current, isStale);
      if (notice) notify(notice.title, notice.body);
    };
    check();
    const timer = window.setInterval(check, 250);
    return () => window.clearInterval(timer);
  }, [enabled, paused]);

  useEffect(() => {
    if (!enabled) return;
    const check = () => {
      const { state: current, stale: isStale } = latest.current;
      if (!current) return;
      const voice = window.speechSynthesis;
      const busy = !!voice && (voice.speaking || voice.pending);
      const announcement = policy.current.next(current, isStale, performance.now(),
        busy ? voicePriority.current + 1 : 0);
      if (!announcement) return;
      voicePriority.current = announcement.priority;
      if (announcement.pattern) beeper.current.play(announcement.pattern);
      say(announcement.message, busy);
    };
    check();
    const timer = window.setInterval(check, 250);
    return () => {
      window.clearInterval(timer);
      window.speechSynthesis?.cancel();
      voicePriority.current = 0;
    };
  }, [enabled]);

  return { enabled, paused, enable, disable };
}
