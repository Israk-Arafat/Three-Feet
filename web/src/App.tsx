import { useEffect, useState } from "react";
import { DemoControls } from "./components/DemoControls";
import { HealthBar } from "./components/HealthBar";
import { Icon } from "./components/Icon";
import { IncidentReport } from "./components/IncidentReport";
import { LiveView } from "./components/LiveView";
import { ObjectsCard } from "./components/ObjectsCard";
import { PowerCard } from "./components/PowerCard";
import { RuleCard } from "./components/RuleCard";
import { StateBanner } from "./components/StateBanner";
import { Timeline } from "./components/Timeline";
import { useAlerts } from "./hooks/useAlerts";
import { useLiveState } from "./hooks/useLiveState";
import { api } from "./lib/api";
import type { AppConfig } from "./types";

const showDemo = new URLSearchParams(location.search).get("demo") === "1";

export default function App() {
  const { state, events, connected, stale } = useLiveState();
  const alerts = useAlerts(state, stale);
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    if (connected) api.config().then(setConfig).catch(() => undefined);
  }, [connected]);

  const simulated = state?.mode === "sim";
  const live = stale ? null : state;

  return (
    <div className="flex min-h-dvh flex-col gap-3 p-3 fit:h-dvh">
      <header className="flex min-h-14 flex-wrap items-center gap-x-6 gap-y-2 border-b-2 border-ink pb-2 xl:h-14 xl:flex-nowrap">
        <a href="/" className="flex items-center gap-3 no-underline" aria-label="Three Feet, home">
          <img src="/favicon.svg" width={32} height={32} alt="" />
          <span className="text-xl font-extrabold tracking-[-0.03em]">Three Feet</span>
        </a>
        <p className="hidden min-w-0 truncate text-sm text-muted lg:block">
          Keeps things three feet from a space heater. Built on SAM 3.1, Muse Spark and Muse Wonder.
        </p>
        <div className="ml-auto flex items-center gap-3">
          {simulated && <span className="tag tag--amber">Simulated scene</span>}
          {state?.mode === "replay" && <span className="tag">Replaying photos</span>}
          <button
            type="button"
            className={`btn ${alerts.enabled ? "" : "btn--primary"}`}
            aria-pressed={alerts.enabled}
            onClick={alerts.enabled ? alerts.disable : alerts.enable}
          >
            <Icon name={alerts.enabled ? "bell" : "bellOff"} size={18} />
            {alerts.enabled ? "Alerts on" : "Turn on alerts"}
          </button>
        </div>
      </header>

      <StateBanner state={state} stale={stale} connected={connected} />

      <main className="layout">
        <LiveView
          className="area-video aspect-video w-full self-start"
          cameraHealthy={!stale && (state?.camera.healthy ?? false)}
          simulated={simulated}
        />
        <ObjectsCard state={live} className="area-objects" />
        <PowerCard state={live} className="area-power" />
        <Timeline events={events} className="area-log" />
        <IncidentReport events={events} openIncident={live?.open_incident ?? null} className="area-incident" />
        <RuleCard rule={state?.rule} className="area-rule" />
      </main>

      <HealthBar state={state} connected={connected && !stale} />
      {showDemo && config?.demo && <DemoControls config={config} />}
    </div>
  );
}
