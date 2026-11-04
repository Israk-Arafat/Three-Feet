import { useCallback, useEffect, useState } from "react";
import { DemoControls } from "./components/DemoControls";
import { Connections } from "./components/Connections";
import { HealthBar } from "./components/HealthBar";
import { HeaterSetup } from "./components/HeaterSetup";
import { Icon } from "./components/Icon";
import { IncidentReport } from "./components/IncidentReport";
import { LiveView } from "./components/LiveView";
import { ObjectsCard } from "./components/ObjectsCard";
import { PowerCard } from "./components/PowerCard";
import { RuleCard } from "./components/RuleCard";
import { StateBanner } from "./components/StateBanner";
import { Timeline } from "./components/Timeline";
import { useAlerts } from "./hooks/useAlerts";
import { useDemoState } from "./hooks/useDemoState";
import { useLiveState } from "./hooks/useLiveState";
import { api } from "./lib/api";
import { DEMO } from "./lib/demo";
import type { AppConfig } from "./types";

const showDemo = new URLSearchParams(location.search).get("demo") === "1";
const REPO_URL = "https://github.com/Israk-Arafat/Three-Feet";
// Fixed for the lifetime of the page, so the hook order never changes.
const useState_ = DEMO ? useDemoState : useLiveState;

export default function App() {
  const { state, events, connected, stale } = useState_();
  const alerts = useAlerts(state, stale);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [setupRevision, setSetupRevision] = useState(0);
  const [demoOpen, setDemoOpen] = useState(false);
  const [aspect, setAspect] = useState(16 / 9);
  const openSetup = useCallback(() => setSetupOpen(true), []);
  const closeSetup = useCallback(() => setSetupOpen(false), []);
  const closeConnections = useCallback(() => setConnectionsOpen(false), []);
  const handleSaved = useCallback(() => setSetupRevision((v) => v + 1), []);

  useEffect(() => {
    if (connected) api.config().then(setConfig).catch(() => undefined);
  }, [connected]);

  const simulated = state?.mode === "sim";
  const live = stale ? null : state;

  return (
    <div className="app-shell flex min-h-dvh flex-col gap-3 p-3">
      <header className="flex min-h-14 flex-wrap items-center gap-x-6 gap-y-2 border-b-2 border-ink pb-2 xl:h-14 xl:flex-nowrap">
        <a href={import.meta.env.BASE_URL} className="flex shrink-0 items-center gap-3 whitespace-nowrap no-underline" aria-label="Three Feet, home">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} width={32} height={32} alt="" />
          <span className="text-xl font-extrabold tracking-[-0.03em]">Three Feet</span>
        </a>
        <p className="hidden min-w-0 truncate text-sm text-muted lg:block">
          A clear view of the space around your heater.
        </p>
        <div className="ml-auto flex items-center gap-3">
          {DEMO && <span className="tag tag--amber" title="A replay of a simulated run. No camera, heater, or plug is connected.">Recorded demo</span>}
          {DEMO && (
            <a className="btn" href={REPO_URL}>
              View on GitHub
            </a>
          )}
          {simulated && <span className="tag tag--amber">Simulated scene</span>}
          {state?.mode === "replay" && <span className="tag">Replaying photos</span>}
          {showDemo && config?.demo && (
            <button type="button" className="btn" aria-pressed={demoOpen} onClick={() => setDemoOpen((v) => !v)}>
              Fault tests
            </button>
          )}
          {!DEMO && (
            <button type="button" className="btn" onClick={() => setConnectionsOpen(true)}>
              <Icon name="link" size={18} />
              Camera and plug
            </button>
          )}
          {!DEMO && (
            <button type="button" className="btn" onClick={openSetup}>
              <Icon name="settings" size={18} />
              Heater setup
            </button>
          )}
          <button
            type="button"
            data-alerts-toggle
            className={`btn ${alerts.enabled ? "" : "btn--primary"}`}
            aria-pressed={alerts.enabled}
            title={alerts.paused ? "Alerts were on. Click anywhere to resume them." : undefined}
            onClick={alerts.enabled ? alerts.disable : alerts.enable}
          >
            <Icon name={alerts.enabled ? "bell" : "bellOff"} size={18} />
            {alerts.enabled ? "Alerts on" : alerts.paused ? "Resume alerts" : "Turn on alerts"}
          </button>
        </div>
      </header>

      <StateBanner state={state} stale={stale} connected={connected} onSetup={openSetup} />

      <main className="layout" data-orientation={aspect < 1 ? "portrait" : "landscape"}>
        <LiveView
          watchMarginIn={state?.watch_margin_in ?? 2}
          className="area-video w-full self-start fit:self-stretch"
          cameraHealthy={!stale && (state?.camera.healthy ?? false)}
          simulated={simulated}
          calibrated={live?.calibration.configured ?? false}
          setupRevision={setupRevision}
          aspect={aspect}
          onSetup={openSetup}
          onAspect={setAspect}
        />
        <ObjectsCard state={live} className="area-objects" />
        <PowerCard state={live} className="area-power" />
        <Timeline events={events} timeZone={state?.time_zone} className="area-log" />
        <IncidentReport events={events} timeZone={state?.time_zone} openIncident={live?.open_incident ?? null} className="area-incident" />
        <RuleCard rule={state?.rule} timeZone={state?.time_zone} className="area-rule" />
      </main>

      <HealthBar state={state} connected={connected && !stale} onSetup={openSetup} />
      {showDemo && config?.demo && <DemoControls config={config} open={demoOpen} onClose={() => setDemoOpen(false)} />}
      <HeaterSetup open={setupOpen} onClose={closeSetup} onSaved={handleSaved} />
      <Connections
        open={connectionsOpen}
        onClose={closeConnections}
        heaterOn={live && live.power.reachable ? live.power.on : null}
      />
    </div>
  );
}
