import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { AppConfig } from "../types";
import { Icon } from "./Icon";

function Toggle({ pressed, onClick, children }: { pressed?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="btn btn--sm" aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-center gap-2 border-t border-rule py-2">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

/** Fault injection for rehearsals and the demo video. Only rendered with ?demo=1 on a --demo server; the header button opens it. */
export function DemoControls({ config, open, onClose }: { config: AppConfig; open: boolean; onClose: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [cameraCut, setCameraCut] = useState(false);
  const [apiDown, setApiDown] = useState(false);
  const [bumped, setBumped] = useState(false);
  const [stuck, setStuck] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [autoplay, setAutoplay] = useState(config.autoplay ?? false);
  const sim = config.mode === "sim";
  const dryrun = config.plug === "dryrun";

  const run = (fn: () => Promise<unknown>, after?: () => void) => async () => {
    setError(null);
    try {
      await fn();
      after?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return null;

  return (
    <aside
      aria-label="Fault injection"
      className="fixed bottom-24 right-4 z-(--z-toast) max-h-[calc(100dvh-8rem)] w-[460px] max-w-[calc(100vw-2rem)] overflow-y-auto border-2 border-ink bg-paper-3 p-4 shadow-card"
    >
      <div className="mb-2 flex items-center justify-between">
        <h2>Fault tests</h2>
        <button type="button" className="btn btn--sm btn--icon" onClick={onClose} aria-label="Hide fault tests">
          <Icon name="x" size={16} />
        </button>
      </div>
      <Row label="Camera">
        <Toggle pressed={cameraCut} onClick={run(() => api.demo.camera(!cameraCut), () => setCameraCut(!cameraCut))}>
          {cameraCut ? "Restore feed" : "Cut feed"}
        </Toggle>
        {sim && (
          <Toggle pressed={bumped} onClick={run(() => api.demo.bump(!bumped), () => setBumped(!bumped))}>
            {bumped ? "Un-bump" : "Bump camera"}
          </Toggle>
        )}
      </Row>
      <Row label="Meta API">
        <Toggle pressed={apiDown} onClick={run(() => api.demo.api(!apiDown), () => setApiDown(!apiDown))}>
          {apiDown ? "Restore API" : "Simulate outage"}
        </Toggle>
      </Row>
      {dryrun && (
        <Row label="Test plug">
          <Toggle pressed={stuck} onClick={run(() => api.demo.plug(stuck ? "unstuck" : "stuck"), () => setStuck(!stuck))}>
            {stuck ? "Unstick relay" : "Stuck relay"}
          </Toggle>
          <Toggle
            pressed={unreachable}
            onClick={run(() => api.demo.plug(unreachable ? "reachable" : "unreachable"), () => setUnreachable(!unreachable))}
          >
            {unreachable ? "Reconnect" : "Unreachable"}
          </Toggle>
          <Toggle onClick={run(() => api.demo.plug("power_on"))}>Heater back on</Toggle>
        </Row>
      )}
      {sim && (
        <>
          <Row label="Script">
            <Toggle pressed={!autoplay} onClick={run(() => api.demo.sim({ autoplay: !autoplay }), () => setAutoplay(!autoplay))}>
              {autoplay ? "Pause script" : "Resume script"}
            </Toggle>
          </Row>
          <Row label="Blanket">
            <Toggle onClick={run(() => api.demo.sim({ object: "blanket", spot: "near" }))}>Too close</Toggle>
            <Toggle onClick={run(() => api.demo.sim({ object: "blanket", spot: "home" }))}>Away</Toggle>
          </Row>
          <Row label="Box">
            <Toggle onClick={run(() => api.demo.sim({ object: "box", spot: "watch" }))}>Near miss</Toggle>
            <Toggle onClick={run(() => api.demo.sim({ object: "box", spot: "near" }))}>Too close</Toggle>
            <Toggle onClick={run(() => api.demo.sim({ object: "box", spot: "home" }))}>Away</Toggle>
          </Row>
          <Row label="Unknown thing">
            <Toggle onClick={run(() => api.demo.sim({ object: "shoe", spot: "near" }))}>Drop at back</Toggle>
            <Toggle onClick={run(() => api.demo.sim({ object: "shoe", spot: "home" }))}>Remove</Toggle>
          </Row>
        </>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-state-warn">
          {error}
        </p>
      )}
    </aside>
  );
}
