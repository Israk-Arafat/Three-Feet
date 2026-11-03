import { useState } from "react";
import { api } from "../lib/api";
import { usd } from "../lib/format";
import type { StateMessage } from "../types";
import { Swatch } from "./ui";

function Cell({ ok, label, value }: { ok: boolean | null; label: string; value: string }) {
  return (
    <div>
      <Swatch tone={ok === null ? "info" : ok ? "ok" : "alert"} />
      <span className="text-muted">{label}</span>
      <span className={`num font-medium ${ok === false ? "text-state-warn" : "text-ink"}`}>{value}</span>
    </div>
  );
}

export function HealthBar({ state, connected }: { state: StateMessage | null; connected: boolean }) {
  const [refMsg, setRefMsg] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  if (!state) {
    return (
      <footer className="strip h-11">
        <div className="text-muted">Connecting to the server.</div>
      </footer>
    );
  }
  const { camera, calibration: cal, api: a } = state;
  const spend = a.spend;
  const cameraValue = camera.healthy
    ? [
        `${camera.fps.toFixed(1)} fps`,
        camera.battery_pct !== null ? `battery ${Math.round(camera.battery_pct)}%` : null,
        camera.wifi_dbm !== null ? `${Math.round(camera.wifi_dbm)} dBm` : null,
      ]
        .filter(Boolean)
        .join(", ")
    : "lost";
  const calValue = !cal.configured
    ? "not calibrated"
    : cal.method === "heater"
    ? `quick setup, ±${cal.distance_err_pct ?? "?"}% of distance`
    : `error ${cal.rms_in?.toFixed(2) ?? "?"} in, ${cal.drift_px !== null ? `drift ${cal.drift_px.toFixed(1)} px` : "markers hidden"}`;
  const apiValue = state.mode === "sim" ? "simulated" : a.ok ? `SAM ${a.sam_latency_s?.toFixed(2) ?? "?"} s` : "unavailable";

  const setReference = async () => {
    setCapturing(true);
    try {
      await api.setReference();
      setRefMsg("saved");
    } catch (e) {
      setRefMsg((e as Error).message);
    } finally {
      setCapturing(false);
    }
  };

  return (
    <footer className="strip min-h-11 overflow-x-auto fit:h-11">
      <Cell ok={connected} label="Server" value={connected ? "connected" : "offline"} />
      <Cell ok={camera.healthy} label="Camera" value={cameraValue} />
      <Cell ok={cal.configured && !state.degraded.includes("CALIBRATION_DRIFT")} label="Calibration" value={calValue} />
      <Cell ok={state.mode === "sim" ? null : a.ok} label="Meta API" value={apiValue} />
      <Cell
        ok={spend.last_hour_usd < spend.budget_usd_per_hour}
        label="Spend"
        value={`${usd(spend.total_usd)}, ${usd(spend.last_hour_usd)} of ${usd(spend.budget_usd_per_hour)} an hour`}
      />
      {state.mode !== "sim" && cal.configured && (
        <div>
          <Swatch tone={cal.reference_set ? "ok" : "watch"} />
          <span className="text-muted">Catch-all</span>
          <span className="font-medium">{cal.reference_set ? "armed" : "needs an empty-zone photo"}</span>
          {!cal.reference_set && (
            <button type="button" className="btn btn--sm" onClick={setReference} disabled={capturing} aria-busy={capturing}>
              Capture
            </button>
          )}
          {refMsg && <span className="text-xs text-muted">{refMsg}</span>}
        </div>
      )}
      {a.concepts.length > 0 && state.mode !== "sim" && (
        <div>
          <span className="text-muted">Looking for</span>
          <span className="font-medium">{a.concepts.join(", ")}</span>
        </div>
      )}
    </footer>
  );
}
