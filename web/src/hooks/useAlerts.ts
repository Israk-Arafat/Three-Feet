import { useCallback, useEffect, useRef, useState } from "react";
import { AlertPolicy, type AlertPattern } from "../lib/alertPolicy";
import { WarningNotifier } from "../lib/warningNotifier";
import { notificationHint, notificationStatus, requestBrowserNotifications, sendBrowserNotification } from "../lib/browserNotifications";
import type { NotificationStatus } from "../lib/browserNotifications";
import type { StateMessage } from "../types";


class Beeper {
  private ctx: AudioContext | null = null;

  unlock() {
    if (!("AudioContext" in window)) return;
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
  const [notificationPermission, setNotificationPermission] = useState<NotificationStatus>(() => notificationStatus());
  const [notificationMessage, setNotificationMessage] = useState<string | null>(null);
  const beeper = useRef(new Beeper());
  const policy = useRef(new AlertPolicy());
  const notifier = useRef(new WarningNotifier());
  const voicePriority = useRef(0);
  const notificationRetryAt = useRef(0);
  const latest = useRef({ state, stale });
  latest.current = { state, stale };

  const requestNotifications = useCallback(async () => {
    const status = await requestBrowserNotifications();
    setNotificationPermission(status);
    return status;
  }, []);

  const testNotification = useCallback(async () => {
    setNotificationMessage(null);
    const status = await requestNotifications();
    if (status !== "granted") {
      setNotificationMessage(notificationHint(status));
      return;
    }
    if (sendBrowserNotification("Three Feet: test notification", "Browser alerts are connected. This is a test; no heater command was sent.", setNotificationMessage)) {
      setNotificationMessage("Test notification requested. If no banner appears, check Windows Do not disturb and your browser's notification settings.");
    }
  }, [requestNotifications]);

  const enable = useCallback(() => {
    beeper.current.unlock();
    void requestNotifications();
    remember(true);
    setPaused(false);
    setEnabled(true);
    // Enabling alerts should not queue a greeting ahead of an actual hazard.
  }, [requestNotifications]);

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
      if (e.target instanceof Element && e.target.closest("[data-alerts-toggle], [data-notification-test]")) return;
      beeper.current.unlock();
      void requestNotifications();
      setPaused(false);
      setEnabled(true);
    };
    window.addEventListener("pointerdown", resume);
    window.addEventListener("keydown", resume);
    return () => {
      window.removeEventListener("pointerdown", resume);
      window.removeEventListener("keydown", resume);
    };
  }, [paused, requestNotifications]);

  useEffect(() => {
    const refresh = () => setNotificationPermission(notificationStatus());
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  // Notifications need no audio unlock, so they stay armed after a reload while alerts are `paused`.
  useEffect(() => {
    if (!enabled && !paused) return;
    const check = () => {
      const { state: current, stale: isStale } = latest.current;
      setNotificationPermission(notificationStatus());
      if (performance.now() < notificationRetryAt.current) return;
      notifier.current.next(current, isStale, notice => {
        const sent = sendBrowserNotification(notice.title, notice.body, setNotificationMessage);
        if (!sent) notificationRetryAt.current = performance.now() + 5000;
        return sent;
      });
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

  return { enabled, paused, enable, disable, notificationPermission, testNotification,
    notificationMessage, notificationHelp: notificationHint(notificationPermission) };
}
