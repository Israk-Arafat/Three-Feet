import { watts } from "../lib/format";
import type { StateMessage } from "../types";
import { Card, Check } from "./ui";

function Sparkline({ values, max }: { values: number[]; max: number }) {
  if (values.length < 2) return <div className="h-12" />;
  const w = 240;
  const h = 48;
  const top = Math.max(max, ...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / top) * (h - 4) - 2}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-12 w-full" aria-hidden>
      <polyline points={pts} fill="none" stroke="#f97316" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function PowerCard({ state }: { state: StateMessage | null }) {
  const p = state?.power;
  const s = state?.signals;
  const shutting = state && ["VERIFYING", "OFF_CONFIRMED", "OFF_UNCONFIRMED"].includes(state.state);
  const confirmedCount = s ? [s.power_off, s.indicator_off].filter((v) => v === true).length : 0;
  return (
    <Card
      title="Heater power"
      chip={<span className="text-xs text-slate-400">plug: {p?.plug ?? "—"}{p?.plug === "dryrun" ? " (simulated)" : ""}</span>}
    >
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-4xl font-black tabular-nums">{p?.reachable ? watts(p.watts) : "—"}</div>
          <div className={`text-sm font-semibold ${!p?.reachable ? "text-red-300" : p.on ? "text-orange-300" : "text-sky-300"}`}>
            {!p?.reachable ? "Plug unreachable" : p.on ? "Plug ON" : "Plug OFF"}
          </div>
        </div>
        <div className="w-1/2">
          <Sparkline values={p?.history ?? []} max={1600} />
        </div>
      </div>
      <div className={`mt-3 rounded-lg p-2 ${shutting ? "bg-slate-800" : "bg-slate-800/40"}`}>
        <div className="mb-1 flex items-center justify-between text-xs uppercase tracking-wider text-slate-400">
          <span>Independent shutoff check</span>
          {shutting && <span className="font-bold text-slate-200">{confirmedCount}/{state?.indicator_configured ? 2 : 1}</span>}
        </div>
        <Check value={shutting ? (s?.power_off ?? null) : null} label="Power draw below 5 W (plug meter)" />
        <Check
          value={shutting && state?.indicator_configured ? (s?.indicator_off ?? null) : null}
          label={state?.indicator_configured ? "Power light dark (camera)" : "Power light check not configured"}
        />
      </div>
    </Card>
  );
}
