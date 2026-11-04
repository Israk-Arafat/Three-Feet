import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { capitalize, humanFlag, measurement } from "../lib/format";
import { hasApproximateScale } from "../lib/calibrationQuality";
import type { SafetyState, StateMessage } from "../types";
import { Icon, type IconName } from "./Icon";

type Tone = "clear" | "watch" | "warn" | "verify" | "off" | "alarm" | "unknown";

const TONE: Record<SafetyState, Tone> = {
  CLEAR: "clear",
  WATCH: "watch",
  WARN: "warn",
  OFF_REQUESTED: "off",
};

const LOOK: Record<Tone, { icon: IconName; title: string }> = {
  clear: { icon: "check", title: "Zone clear" },
  watch: { icon: "near", title: "Something is near" },
  warn: { icon: "alert", title: "Too close to the heater" },
  verify: { icon: "power", title: "Sending power command" },
  off: { icon: "power", title: "Power off requested" },
  alarm: { icon: "alert", title: "Power-off request failed" },
  unknown: { icon: "question", title: "Unknown, not safe" },
};

/** The signature: one sign whose status block, words and countdown change with the safety state. */
export function StateBanner({
  state,
  stale,
  connected,
  onSetup,
}: {
  state: StateMessage | null;
  stale: boolean;
  connected: boolean;
  onSetup?: () => void;
}) {
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const live = state && !stale ? state : null;
  const blind = live ? live.degraded.length > 0 : true;
  const monitoring = live ? ["CLEAR", "WATCH", "WARN"].includes(live.state) : false;
  const trackedWarning = live?.state === "WARN" && live.objects.some(o => o.status === "violation")
    && live.degraded.every(flag => flag === "API_DOWN");
  const weakCalibration = hasApproximateScale(live);
  const tone: Tone = !live ? "unknown" : live.reset_pending || live.off_pending ? "verify"
    : live.off_error && live.state === "OFF_REQUESTED" ? "alarm"
    : trackedWarning ? "warn"
    : blind && monitoring ? "unknown"
    : weakCalibration && live.state === "CLEAR" ? "watch" : TONE[live.state] ?? "off";
  const look = LOOK[tone];
  const needsSetup = !!live && !live.calibration.configured;
  const worst = live?.objects[0];
  const title = live?.reset_pending
    ? live.reset_cancelled ? "Cancelling power restoration" : "Turning heater on"
    : !state ? "Connecting to Three Feet"
    : needsSetup && tone === "unknown" ? "Set up your heater"
    : weakCalibration && live?.state === "CLEAR" && tone === "watch" ? "Monitoring · approximate scale"
    : worst?.status === "pending" && (tone === "unknown" || tone === "watch") ? "Waiting for SAM identification"
    : worst?.is_person && tone === "watch" ? "Person detected"
    : tone === "watch" && worst?.status === "uncertain" ? "Outside zone, distance uncertain"
    : look.title;

  let detail: string;
  if (live?.reset_pending) {
    detail = live.reset_cancelled
      ? "Power restoration stopped. Requesting power off."
      : "Restoring plug power and checking the clearance zone.";
  } else if (!state) {
    detail = connected ? "Waiting for live data." : "Reaching the server.";
  } else if (!live) {
    detail = connected ? "Waiting for live data." : "Lost the server. Reconnecting.";
  } else if (tone === "unknown") {
    detail = needsSetup
      ? "Add the base dimensions to map its danger zone."
      : worst?.status === "pending" ? `Movement detected; no shutoff countdown until SAM identifies it. ${live.api.health_reason ?? ""}`
      : live.api.health_reason ?? capitalize(live.degraded.map(humanFlag).join(", ")) + ".";
  } else if (weakCalibration && live.state === "CLEAR" && tone === "watch") {
    detail = "The displayed red outline is the active trigger zone. Inch distances and the zone’s physical size are approximate; see calibration details below.";
  } else if ((tone === "warn" || tone === "watch") && worst) {
    detail = `${capitalize(worst.label)}, ${measurement(worst.distance_in, worst.err_in)} from the ${worst.side}. Needs ${worst.required_in.toFixed(0)} in.`;
    if (tone === "watch") detail += worst.is_person ? " Person detected; no object-shutoff countdown."
      : worst.status === "pending" ? " Waiting for SAM; no shutoff countdown."
      : " Outside the red zone; no shutoff countdown.";
  } else if (tone === "clear") {
    detail = worst
      ? `Closest: ${worst.label}, ${measurement(worst.distance_in, worst.err_in)} from the ${worst.side}.`
      : "Nothing near the heater.";
  } else if (tone === "verify") {
    detail = "Sending an OFF command to the plug.";
  } else if (tone === "off") {
    detail = "An OFF command was sent to the plug. Clear the zone before resetting.";
  } else {
    detail = "The OFF command failed after retries. Check the heater now.";
  }

  // A background tab shows the verdict, and a warning mark when it needs attention.
  const needsAttention = !!state && ["warn", "verify", "alarm", "unknown"].includes(tone);
  useEffect(() => {
    document.title = state ? `${needsAttention ? "⚠ " : ""}${title} · Three Feet` : "Three Feet: heater clearance monitor";
  }, [state, title, needsAttention]);

  const countLabel = tone === "warn" ? "Power off in" : tone === "unknown" ? "Fail-safe in" : null;
  const showCount = live && countLabel && live.countdown_s !== null;
  const latched = live?.state === "OFF_REQUESTED";

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

  let resetHint = "";
  if (live) {
    if (!live.calibration.configured) {
      resetHint = "Calibrate the heater base first.";
    } else if (live.degraded.includes("CALIBRATION_DRIFT")) {
      resetHint = "Recalibrate before turning on.";
    } else if (!live.camera.healthy || live.degraded.includes("CAMERA_LOST")) {
      resetHint = "Needs a working camera.";
    } else if (!live.api.ok || live.degraded.includes("API_DOWN")) {
      resetHint = "Wait for object detection to recover.";
    } else if (!live.power.reachable || live.degraded.includes("PLUG_UNREACHABLE")) {
      resetHint = "Reconnect the plug first.";
    } else if (!live.can_reset) {
      resetHint = "Clear the zone first.";
    } else {
      resetHint = "Restores power and resumes monitoring.";
    }
  }

  return (
    <section className="sign" data-tone={tone} aria-label="Heater safety status">
      <span className="sign__mark">
        <Icon name={look.icon} size={44} strokeWidth={2.5} />
      </span>
      {/* Only the verdict is announced; the ticking countdown beside it stays out of the live region. */}
      <div className="min-w-0" role="status" aria-live={needsAttention ? "assertive" : "polite"} aria-atomic="true">
        <h1 className="sign__title">{title}</h1>
        <p className="sign__detail">{detail}</p>
        {live && <p className="sign__power">
          Plug: {!live.power.reachable ? "unreachable" : live.power.on === null ? "unknown" : live.power.on ? "on" : "reports off"}
        </p>}
        {weakCalibration && live?.state !== "CLEAR" && monitoring && <p className="sign__power">Approximate scale · see calibration details below.</p>}
      </div>
      <div className="sign__side">
        {needsSetup && onSetup && (
          <button type="button" className="btn btn--primary self-center" onClick={onSetup}>
            Set up heater base
          </button>
        )}
        {showCount && (
          <div className="sign__count plate">
            <span className="sign__count-label">{countLabel}</span>
            <span className="figure num">{Math.ceil(live.countdown_s ?? 0)} s</span>
          </div>
        )}
        {latched && live && (
          <>
            <div className="flex flex-col justify-center gap-1">
              <button
                type="button"
                className="btn btn--primary"
                onClick={onReset}
                disabled={!live.can_reset || resetting}
                aria-busy={resetting}
              >
                {resetting ? "Turning on" : "Reset and turn on"}
              </button>
              <span className="text-xs font-medium">{resetError ?? resetHint}</span>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
