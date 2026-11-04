import { watts } from "../lib/format";
import type { StateMessage } from "../types";
import { CheckRow, Panel } from "./ui";

function Sparkline({ values, max }: { values: number[]; max: number }) {
  if (values.length < 2) return <div className="h-12" />;
  const w = 240;
  const h = 48;
  const top = Math.max(max, ...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / top) * (h - 4) - 2}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-12 w-full text-ink" role="img" aria-label="Power draw over the last 30 seconds">
        <line x1="0" x2={w} y1={h - 1} y2={h - 1} stroke="var(--color-rule)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        <polygon points={`0,${h - 1} ${pts} ${w},${h - 1}`} fill="currentColor" opacity="0.1" />
        <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="mt-0.5 flex justify-between text-xs text-muted" aria-hidden="true">
        <span>30 s ago</span>
        <span>now</span>
      </div>
    </div>
  );
}

export function PowerCard({ state, className = "" }: { state: StateMessage | null; className?: string }) {
  const p = state?.power;
  const s = state?.signals;
  const shutting = state ? ["VERIFYING", "OFF_CONFIRMED", "OFF_UNCONFIRMED"].includes(state.state) : false;
  const total = state?.indicator_configured ? 2 : 1;
  const passed = s ? [s.power_off, s.indicator_off].filter((v) => v === true).length : 0;
  const plugNote = p ? `Plug: ${p.plug === "dryrun" ? "simulated" : p.plug}${p.metering ? "" : ", no meter"}` : undefined;
  const metering = p?.metering ?? true;
  const headline = !p?.reachable ? "No reading" : metering ? watts(p.watts) : p.on === null ? "Unknown" : p.on ? "On" : "Off";

  return (
    <Panel title="Heater power" note={plugNote} className={className}>
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="num whitespace-nowrap text-4xl font-extrabold leading-none">{headline}</div>
          <div className={`mt-1 text-sm font-medium ${!p?.reachable ? "text-state-warn" : "text-muted"}`}>
            {!p?.reachable ? "Plug unreachable" : metering ? (p.on ? "Plug on" : "Plug off") : "As the plug reports it"}
          </div>
        </div>
        {metering && (
          <div className="w-1/2">
            <Sparkline values={p?.history ?? []} max={1600} />
          </div>
        )}
      </div>
      <div className="mt-4 border-t border-rule pt-3">
        <div className="mb-1 flex items-baseline justify-between text-sm">
          <h3>Shutoff check</h3>
          <span className="num font-medium text-muted">{shutting ? `${passed} of ${total} passed` : "Runs after a power cut"}</span>
        </div>
        <CheckRow
          value={shutting ? (s?.power_off ?? null) : null}
          label={metering ? "Power under 5 W, from the plug meter" : "Plug reports off (it has no power meter)"}
        />
        <CheckRow
          value={shutting && state?.indicator_configured ? (s?.indicator_off ?? null) : null}
          label={state?.indicator_configured ? "Power light dark, from the camera" : "Power light check not set up"}
        />
      </div>
    </Panel>
  );
}
