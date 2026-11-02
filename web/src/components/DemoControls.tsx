import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { AppConfig } from "../types";

function Btn({ children, onClick, active }: { children: ReactNode; onClick: () => void; active?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
        active ? "bg-yellow-400 text-slate-950" : "bg-slate-700 text-slate-100 hover:bg-slate-600"
      }`}
    >
      {children}
    </button>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-24 text-xs text-slate-400">{label}</span>
      {children}
    </div>
  );
}

/** Fault injection for rehearsals and the demo video. Only rendered with ?demo=1 on a --demo server. */
export function DemoControls({ config }: { config: AppConfig }) {
  const [open, setOpen] = useState(true);
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

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-16 right-4 rounded-full bg-yellow-400 px-3 py-1.5 text-xs font-bold text-slate-950 shadow-lg"
      >
        Demo controls
      </button>
    );
  }

  return (
    <div className="fixed bottom-16 right-4 z-40 w-[440px] space-y-2 rounded-xl border border-yellow-400/60 bg-slate-950/95 p-3 shadow-2xl">
      <div className="flex items-center justify-between">
        <span className="text-xs font-black tracking-widest text-yellow-300">FAULT INJECTION / DEMO</span>
        <button onClick={() => setOpen(false)} className="text-slate-400 hover:text-white">
          ✕
        </button>
      </div>
      <Row label="Camera">
        <Btn active={cameraCut} onClick={run(() => api.demo.camera(!cameraCut), () => setCameraCut(!cameraCut))}>
          {cameraCut ? "Restore feed" : "Cut feed"}
        </Btn>
        {sim && (
          <Btn active={bumped} onClick={run(() => api.demo.bump(!bumped), () => setBumped(!bumped))}>
            {bumped ? "Un-bump" : "Bump camera"}
          </Btn>
        )}
      </Row>
      <Row label="Meta API">
        <Btn active={apiDown} onClick={run(() => api.demo.api(!apiDown), () => setApiDown(!apiDown))}>
          {apiDown ? "Restore API" : "Simulate outage"}
        </Btn>
      </Row>
      {dryrun && (
        <Row label="Dry-run plug">
          <Btn active={stuck} onClick={run(() => api.demo.plug(stuck ? "unstuck" : "stuck"), () => setStuck(!stuck))}>
            {stuck ? "Unstick relay" : "Stuck relay"}
          </Btn>
          <Btn
            active={unreachable}
            onClick={run(() => api.demo.plug(unreachable ? "reachable" : "unreachable"), () => setUnreachable(!unreachable))}
          >
            {unreachable ? "Reconnect" : "Unreachable"}
          </Btn>
          <Btn onClick={run(() => api.demo.plug("power_on"))}>Person turns heater on</Btn>
        </Row>
      )}
      {sim && (
        <>
          <Row label="Autoplay">
            <Btn active={!autoplay} onClick={run(() => api.demo.sim({ autoplay: !autoplay }), () => setAutoplay(!autoplay))}>
              {autoplay ? "Pause script" : "Resume script"}
            </Btn>
          </Row>
          <Row label="Blanket">
            <Btn onClick={run(() => api.demo.sim({ object: "blanket", spot: "near" }))}>Too close</Btn>
            <Btn onClick={run(() => api.demo.sim({ object: "blanket", spot: "home" }))}>Away</Btn>
          </Row>
          <Row label="Box">
            <Btn onClick={run(() => api.demo.sim({ object: "box", spot: "watch" }))}>Near miss</Btn>
            <Btn onClick={run(() => api.demo.sim({ object: "box", spot: "near" }))}>Too close</Btn>
            <Btn onClick={run(() => api.demo.sim({ object: "box", spot: "home" }))}>Away</Btn>
          </Row>
          <Row label="Unknown obj.">
            <Btn onClick={run(() => api.demo.sim({ object: "shoe", spot: "near" }))}>Drop near back</Btn>
            <Btn onClick={run(() => api.demo.sim({ object: "shoe", spot: "home" }))}>Remove</Btn>
          </Row>
        </>
      )}
      {error && <p className="text-xs text-red-300">{error}</p>}
    </div>
  );
}
