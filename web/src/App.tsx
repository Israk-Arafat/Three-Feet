import { useEffect, useState } from "react";
import { DemoControls } from "./components/DemoControls";
import { HealthBar } from "./components/HealthBar";
import { IncidentReport } from "./components/IncidentReport";
import { LiveView } from "./components/LiveView";
import { ObjectsCard } from "./components/ObjectsCard";
import { PowerCard } from "./components/PowerCard";
import { RuleCard } from "./components/RuleCard";
import { StateBanner } from "./components/StateBanner";
import { Timeline } from "./components/Timeline";
import { ModelChip } from "./components/ui";
import { useAlerts } from "./hooks/useAlerts";
import { useLiveState } from "./hooks/useLiveState";
import { api } from "./lib/api";
import type { AppConfig } from "./types";

const showDemo = new URLSearchParams(location.search).get("demo") === "1";

export default function App() {
  const { state, events, connected, stale } = useLiveState();
  const alerts = useAlerts(state, stale);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [countdownTotal, setCountdownTotal] = useState(10);
  const [prevState, setPrevState] = useState<string | null>(null);

  useEffect(() => {
    if (connected) api.config().then(setConfig).catch(() => undefined);
  }, [connected]);

  // Remember the dwell length when a warning starts so the countdown ring drains from full.
  if (state && state.state !== prevState) {
    setPrevState(state.state);
    if (state.state === "WARN" && state.countdown_s) setCountdownTotal(state.countdown_s);
  }

  const simulated = state?.mode === "sim";

  return (
    <div className="flex min-h-screen flex-col gap-3 p-3 xl:h-screen">
      <header className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <span className="text-xl font-black tracking-tight">Three Feet</span>
          <span className="text-sm text-slate-400">space-heater clearance monitor</span>
        </div>
        <div className="ml-4 hidden items-center gap-1.5 lg:flex">
          <span className="text-xs text-slate-500">Powered by</span>
          <ModelChip name="SAM 3.1" />
          <ModelChip name="Muse Spark" />
          <ModelChip name="Muse Wonder" />
        </div>
        <div className="ml-auto flex items-center gap-2">
          {simulated && <span className="rounded bg-yellow-400 px-2 py-1 text-xs font-black text-slate-950">SIMULATION</span>}
          {state?.mode === "replay" && <span className="rounded bg-slate-700 px-2 py-1 text-xs font-bold">REPLAY</span>}
          <button
            onClick={alerts.enabled ? alerts.disable : alerts.enable}
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
              alerts.enabled ? "bg-slate-800 text-slate-200" : "animate-pulse bg-amber-400 text-slate-950"
            }`}
          >
            {alerts.enabled ? "🔔 Alerts on" : "🔕 Enable sound & voice alerts"}
          </button>
        </div>
      </header>

      <StateBanner state={state} stale={stale} connected={connected} countdownTotal={countdownTotal} />

      <main className="grid flex-1 grid-cols-1 gap-3 xl:min-h-0 xl:grid-cols-[minmax(0,1fr)_460px]">
        <div className="flex flex-col gap-3 xl:min-h-0">
          <div className="flex aspect-video xl:aspect-auto xl:min-h-[320px] xl:flex-[3]">
            <div className="flex w-full">
              <LiveView cameraHealthy={!stale && (state?.camera.healthy ?? false)} simulated={simulated} />
            </div>
          </div>
          <div className="grid h-[320px] grid-cols-1 gap-3 md:grid-cols-2 xl:h-auto xl:min-h-[180px] xl:flex-[1.2]">
            <Timeline events={events} />
            <IncidentReport events={events} openIncident={state?.open_incident ?? null} />
          </div>
        </div>
        <aside className="flex flex-col gap-3 xl:min-h-0 xl:overflow-y-auto">
          <ObjectsCard state={stale ? null : state} />
          <RuleCard rule={state?.rule} />
          <PowerCard state={stale ? null : state} />
        </aside>
      </main>

      <HealthBar state={state} connected={connected && !stale} />
      {showDemo && config?.demo && <DemoControls config={config} />}
    </div>
  );
}
