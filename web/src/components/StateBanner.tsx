import { useState } from "react";
import { api } from "../lib/api";
import { humanFlag } from "../lib/format";
import type { SafetyState, StateMessage } from "../types";

const LOOK: Record<SafetyState | "UNKNOWN", { bg: string; icon: string; title: string }> = {
  CLEAR: { bg: "bg-emerald-700", icon: "✓", title: "Zone clear" },
  WATCH: { bg: "bg-amber-400 text-slate-950", icon: "!", title: "Object near the zone" },
  WARN: { bg: "bg-red-600 animate-[pulse_1.6s_ease-in-out_infinite]", icon: "⚠", title: "Too close to the heater" },
  VERIFYING: { bg: "bg-red-700", icon: "⏻", title: "Power cut sent · verifying" },
  OFF_CONFIRMED: { bg: "bg-sky-700", icon: "⏻", title: "Heater OFF — confirmed" },
  OFF_UNCONFIRMED: {
    bg: "bg-red-600 animate-[flash_0.8s_steps(2)_infinite]",
    icon: "⚠",
    title: "Shutoff NOT confirmed — check the heater now",
  },
  UNKNOWN: { bg: "stripes bg-slate-700", icon: "?", title: "UNKNOWN — not safe" },
};

function CountdownRing({ seconds, total }: { seconds: number; total: number }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, seconds / Math.max(total, 0.1)));
  return (
    <div className="relative h-16 w-16 shrink-0" aria-label={`${Math.ceil(seconds)} seconds`}>
      <svg viewBox="0 0 64 64" className="h-16 w-16 -rotate-90">
        <circle cx="32" cy="32" r={r} fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="6" />
        <circle
          cx="32"
          cy="32"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - frac)}
          style={{ transition: "stroke-dashoffset 0.25s linear" }}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-xl font-black tabular-nums">
        {Math.ceil(seconds)}
      </span>
    </div>
  );
}

export function StateBanner({
  state,
  stale,
  connected,
  countdownTotal,
}: {
  state: StateMessage | null;
  stale: boolean;
  connected: boolean;
  countdownTotal: number;
}) {
  const [resetError, setResetError] = useState<string | null>(null);
  const degraded = !state || stale || state.degraded.some((f) => f !== "API_DOWN");
  const monitoring = state && ["CLEAR", "WATCH", "WARN"].includes(state.state);
  const key: SafetyState | "UNKNOWN" = !state || stale ? "UNKNOWN" : degraded && monitoring ? "UNKNOWN" : state.state;
  const look = LOOK[key];

  let detail = state?.reason ?? "";
  if (!state || stale) detail = connected ? "Waiting for live data…" : "Backend disconnected — reconnecting…";
  else if (key === "UNKNOWN") detail = state.degraded.map(humanFlag).join(" · ") + (state.reason ? ` — ${state.reason}` : "");
  const worst = state?.objects[0];
  if (state && !stale && (key === "WARN" || key === "WATCH") && worst) {
    detail = `${worst.label} ${worst.distance_in.toFixed(1)} in from ${worst.side} · needs ${worst.required_in.toFixed(0)} in`;
  } else if (state && !stale && key === "CLEAR") {
    detail = worst
      ? `Closest: ${worst.label}, ${worst.distance_in.toFixed(1)} in from the ${worst.side}`
      : "Nothing near the heater";
  }
  const showCountdown = state && !stale && state.countdown_s !== null && ["WARN", "UNKNOWN", "VERIFYING"].includes(key);

  const onReset = async () => {
    setResetError(null);
    try {
      await api.reset();
    } catch (e) {
      setResetError((e as Error).message);
    }
  };

  return (
    <div className={`flex items-center gap-5 rounded-xl px-6 py-4 shadow-lg ${look.bg}`} role="status" aria-live="assertive">
      <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-black/25 text-4xl font-black">
        {look.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-3xl font-black tracking-tight">{look.title}</div>
        <div className="truncate text-xl font-medium opacity-95">{detail}</div>
      </div>
      {showCountdown && state && (
        <div className="flex items-center gap-3">
          <span className="text-right text-sm font-semibold uppercase leading-tight opacity-90">
            {key === "WARN" ? "power off in" : key === "VERIFYING" ? "verifying" : "fail-safe in"}
          </span>
          <CountdownRing seconds={state.countdown_s ?? 0} total={key === "UNKNOWN" ? 30 : key === "VERIFYING" ? 5 : countdownTotal} />
        </div>
      )}
      {state && (state.state === "OFF_CONFIRMED" || state.state === "OFF_UNCONFIRMED") && (
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={onReset}
            disabled={!state.can_reset || stale}
            className="rounded-lg bg-white px-5 py-3 text-lg font-bold text-slate-900 shadow disabled:cursor-not-allowed disabled:opacity-40"
            title={state.can_reset ? "Resume monitoring" : "Clear the zone first — reset is refused while anything is too close"}
          >
            Reset
          </button>
          <span className="text-xs opacity-90">
            {state.can_reset
              ? "Zone clear — you may reset"
              : !state.camera.healthy
                ? "Camera must be working to reset"
                : "Clear the zone to enable reset"}
          </span>
          {resetError && <span className="text-xs font-semibold">{resetError}</span>}
        </div>
      )}
    </div>
  );
}
