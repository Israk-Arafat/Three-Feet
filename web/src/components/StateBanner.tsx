import { useState } from "react";
import { api } from "../lib/api";
import { capitalize, humanFlag } from "../lib/format";
import type { SafetyState, StateMessage } from "../types";
import { Icon, type IconName } from "./Icon";

type Tone = "clear" | "watch" | "warn" | "verify" | "off" | "alarm" | "unknown";

const TONE: Record<SafetyState, Tone> = {
  CLEAR: "clear",
  WATCH: "watch",
  WARN: "warn",
  VERIFYING: "verify",
  OFF_CONFIRMED: "off",
  OFF_UNCONFIRMED: "alarm",
};

const LOOK: Record<Tone, { icon: IconName; title: string }> = {
  clear: { icon: "check", title: "Zone clear" },
  watch: { icon: "near", title: "Something is near" },
  warn: { icon: "alert", title: "Too close to the heater" },
  verify: { icon: "power", title: "Power cut, checking" },
  off: { icon: "power", title: "Heater off, confirmed" },
  alarm: { icon: "alert", title: "Not confirmed: check the heater" },
  unknown: { icon: "question", title: "Unknown, not safe" },
};

/** The signature: one sign whose status block, words and countdown change with the safety state. */
export function StateBanner({
  state,
  stale,
  connected,
}: {
  state: StateMessage | null;
  stale: boolean;
  connected: boolean;
}) {
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const live = state && !stale ? state : null;
  const blind = live ? live.degraded.some((f) => f !== "API_DOWN") : true;
  const monitoring = live ? ["CLEAR", "WATCH", "WARN"].includes(live.state) : false;
  const tone: Tone = !live ? "unknown" : blind && monitoring ? "unknown" : TONE[live.state];
  const look = LOOK[tone];
  const worst = live?.objects[0];

  let detail: string;
  if (!live) {
    detail = connected ? "Waiting for live data." : "Lost the server. Reconnecting.";
  } else if (tone === "unknown") {
    detail = capitalize(live.degraded.filter((f) => f !== "API_DOWN").map(humanFlag).join(", ")) + ".";
  } else if ((tone === "warn" || tone === "watch") && worst) {
    detail = `${capitalize(worst.label)}, ${worst.distance_in.toFixed(1)} in from the ${worst.side}. Needs ${worst.required_in.toFixed(0)} in.`;
  } else if (tone === "clear") {
    detail = worst
      ? `Closest: ${worst.label}, ${worst.distance_in.toFixed(1)} in from the ${worst.side}.`
      : "Nothing near the heater.";
  } else if (tone === "verify") {
    detail = `${capitalize(live.reason)}.`;
  } else if (tone === "off") {
    const first = live.power.metering ? "Power is under 5 W" : "The plug reports off";
    detail = live.signals.indicator_off ? `${first} and the light is dark.` : `${first}.`;
  } else {
    detail = "Power did not drop after retrying. Go to the heater now.";
  }

  const countLabel = tone === "warn" ? "Power off in" : tone === "verify" ? "Checking" : tone === "unknown" ? "Fail-safe in" : null;
  const showCount = live && countLabel && live.countdown_s !== null;
  const latched = live && (live.state === "OFF_CONFIRMED" || live.state === "OFF_UNCONFIRMED");
  const signalsPassed = live ? [live.signals.power_off, live.signals.indicator_off].filter((v) => v === true).length : 0;

  const onReset = async () => {
    setResetError(null);
    setResetting(true);
    try {
      await api.reset();
    } catch (e) {
      setResetError((e as Error).message);
    } finally {
      setResetting(false);
    }
  };

  const resetHint = !live
    ? ""
    : live.can_reset
      ? "The zone is clear."
      : !live.camera.healthy
        ? "Needs a working camera."
        : "Clear the zone first.";

  return (
    <section className="sign fit:h-28" data-tone={tone} role="status" aria-live="assertive">
      <span className="sign__mark">
        <Icon name={look.icon} size={44} strokeWidth={2.5} />
      </span>
      <div className="min-w-0">
        <h1 className="sign__title">{look.title}</h1>
        <p className="sign__detail">{detail}</p>
      </div>
      <div className="sign__side">
        {showCount && (
          <div className="sign__count plate">
            <span className="sign__count-label">{countLabel}</span>
            <span className="figure num">{Math.ceil(live.countdown_s ?? 0)} s</span>
          </div>
        )}
        {latched && live && (
          <>
            {live.state === "OFF_CONFIRMED" && (
              <div className="sign__count plate">
                <span className="sign__count-label">Checks passed</span>
                <span className="figure num">
                  {signalsPassed}/{live.indicator_configured ? 2 : 1}
                </span>
              </div>
            )}
            <div className="flex flex-col justify-center gap-1">
              <button
                type="button"
                className="btn btn--primary"
                onClick={onReset}
                disabled={!live.can_reset || resetting}
                aria-busy={resetting}
              >
                {resetting ? "Resetting" : "Reset"}
              </button>
              <span className="text-xs font-medium">{resetError ?? resetHint}</span>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
