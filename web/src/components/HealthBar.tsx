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

export function HealthBar({ state, connected, onSetup }: { state: StateMessage | null; connected: boolean; onSetup?: () => void }) {
  const [refMsg, setRefMsg] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  if (!state) {
    return (
      <footer className="strip">
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
        camera.wifi_dbm !== null && camera.wifi_dbm < 0 ? `${Math.round(camera.wifi_dbm)} dBm` : null,
      ]
        .filter(Boolean)
        .join(", ")
    : "lost";
  const calValue = !cal.configured
    ? "not calibrated"
    : cal.method === "heater"
    ? `heater base${cal.distance_err_pct !== null ? ` · ${cal.distance_err_pct}% distance uncertainty` : ""}`
    : `error ${cal.rms_in?.toFixed(2) ?? "?"} in, ${cal.drift_px !== null ? `drift ${cal.drift_px.toFixed(1)} px` : "markers hidden"}`;
  const apiValue = state.mode === "sim" ? "simulated" : a.ok
    ? a.local_tracking
      ? `Tracking locally; SAM checked ${a.verification_age_s?.toFixed(0) ?? "?"} s ago`
      : `SAM ${a.sam_latency_s?.toFixed(2) ?? "?"} s · result ${a.result_age_s?.toFixed(1) ?? "?"} s old`
    : a.health_reason ?? "unavailable";

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
    <footer className="strip">
      <Cell ok={connected} label="Server" value={connected ? "connected" : "offline"} />
      <Cell ok={camera.healthy} label="Camera" value={cameraValue} />
      <Cell ok={!cal.configured || state.degraded.includes("CALIBRATION_DRIFT") ? false
        : (cal.distance_err_pct ?? 0) >= 30 ? null : true} label="Calibration" value={calValue} />
      {onSetup && <div><button type="button" className="btn btn--sm" onClick={onSetup}>{cal.configured ? "Edit heater base" : "Set up heater base"}</button></div>}
      <Cell ok={state.mode === "sim" ? null : a.ok} label="Meta API" value={apiValue} />
      <div title={`${usd(spend.total_usd)} spent in total`}>
        <Swatch tone={spend.last_hour_usd < spend.budget_usd_per_hour ? "ok" : "alert"} />
        <span className="text-muted">Spend</span>
        <span className={`num font-medium ${spend.last_hour_usd < spend.budget_usd_per_hour ? "text-ink" : "text-state-warn"}`}>
          {usd(spend.last_hour_usd)} of {usd(spend.budget_usd_per_hour)} this hour
        </span>
      </div>
      {state.mode !== "sim" && cal.configured && (
        <div>
          <Swatch tone={cal.reference_set ? "ok" : "watch"} />
          <span className="text-muted">Catch-all</span>
          <span className="font-medium">{cal.reference_set ? "armed" : "needs an empty-zone photo"}</span>
          {!cal.reference_set && (
            <button type="button" className="btn btn--sm" onClick={setReference} disabled={capturing || !connected || !camera.healthy || state.degraded.includes("CALIBRATION_DRIFT")} aria-busy={capturing} title="Clear all objects from the danger zone before capturing its reference photo">
              Capture empty zone
            </button>
          )}
          {refMsg && <span className="text-xs text-muted">{refMsg}</span>}
        </div>
      )}
      {a.concepts.length > 0 && state.mode !== "sim" && (
        <div title={a.concepts.join(", ")}>
          <span className="text-muted">Looking for</span>
          <span className="truncate font-medium">{a.concepts.join(", ")}</span>
        </div>
      )}
    </footer>
  );
}
