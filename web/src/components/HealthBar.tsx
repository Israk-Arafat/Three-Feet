import { useState } from "react";
import { api } from "../lib/api";
import { usd } from "../lib/format";
import type { StateMessage } from "../types";

function Item({ ok, label, value }: { ok: boolean | null; label: string; value: string }) {
  const dot = ok === null ? "bg-slate-500" : ok ? "bg-emerald-400" : "bg-red-500";
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      <span className="text-slate-400">{label}</span>
      <span className="font-semibold tabular-nums text-slate-100">{value}</span>
    </div>
  );
}

export function HealthBar({ state, connected }: { state: StateMessage | null; connected: boolean }) {
  const [refMsg, setRefMsg] = useState<string | null>(null);
  if (!state) {
    return <footer className="rounded-xl bg-slate-900 px-4 py-2 text-sm text-slate-400">Connecting…</footer>;
  }
  const { camera, calibration: cal, api: a } = state;
  const spend = a.spend;
  const cameraValue = camera.healthy
    ? `${camera.fps.toFixed(1)} fps${camera.battery_pct !== null ? ` · ${Math.round(camera.battery_pct)}%` : ""}${
        camera.wifi_dbm !== null ? ` · ${Math.round(camera.wifi_dbm)} dBm` : ""
      }`
    : "lost";
  const calValue = !cal.configured
    ? "not calibrated"
    : `RMS ${cal.rms_in?.toFixed(2) ?? "—"} in${cal.drift_px !== null ? ` · drift ${cal.drift_px.toFixed(1)} px` : " · markers hidden"}`;
  const apiValue = state.mode === "sim" ? "simulated" : a.ok ? `SAM ${a.sam_latency_s?.toFixed(2) ?? "—"} s` : "unavailable";

  const setReference = async () => {
    try {
      await api.setReference();
      setRefMsg("saved");
    } catch (e) {
      setRefMsg((e as Error).message);
    }
  };

  return (
    <footer className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-xl bg-slate-900 px-4 py-2 text-sm">
      <Item ok={connected} label="Server" value={connected ? "connected" : "offline"} />
      <Item ok={camera.healthy} label="Camera" value={cameraValue} />
      <Item ok={cal.configured && !state.degraded.includes("CALIBRATION_DRIFT")} label="Calibration" value={calValue} />
      <Item ok={state.mode === "sim" ? null : a.ok} label="Meta API" value={apiValue} />
      <Item
        ok={spend.last_hour_usd < spend.budget_usd_per_hour}
        label="Spend"
        value={`${usd(spend.total_usd)} · ${usd(spend.last_hour_usd)}/${usd(spend.budget_usd_per_hour)} per hr`}
      />
      {state.mode !== "sim" && cal.configured && (
        <div className="flex items-center gap-2">
          <Item ok={cal.reference_set} label="Catch-all" value={cal.reference_set ? "armed" : "no empty-zone reference"} />
          {!cal.reference_set && (
            <button onClick={setReference} className="rounded bg-slate-700 px-2 py-0.5 text-xs font-semibold hover:bg-slate-600">
              Capture now (zone must be empty)
            </button>
          )}
          {refMsg && <span className="text-xs text-slate-400">{refMsg}</span>}
        </div>
      )}
      {a.concepts.length > 0 && state.mode !== "sim" && (
        <div className="truncate text-slate-400">
          Looking for: <span className="text-slate-200">{a.concepts.join(", ")}</span>
        </div>
      )}
    </footer>
  );
}
