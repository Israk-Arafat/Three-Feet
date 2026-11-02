import { useEffect, useState } from "react";

/** Annotated MJPEG from the backend. Reconnects automatically if the stream drops. */
export function LiveView({ cameraHealthy, simulated }: { cameraHealthy: boolean; simulated: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!failed) return;
    const id = window.setTimeout(() => {
      setFailed(false);
      setAttempt((a) => a + 1);
    }, 2000);
    return () => window.clearTimeout(id);
  }, [failed]);

  return (
    <div className="relative flex min-h-0 items-center justify-center overflow-hidden rounded-xl border border-slate-800 bg-black">
      {!failed && (
        <img
          key={attempt}
          src={`/stream.mjpg?a=${attempt}`}
          alt="Live camera view with clearance zone and detected objects"
          className={`max-h-full max-w-full object-contain ${cameraHealthy ? "" : "opacity-40"}`}
          onError={() => setFailed(true)}
        />
      )}
      {failed && <div className="p-10 text-xl text-slate-400">Video stream unavailable — retrying…</div>}
      <div className="pointer-events-none absolute left-3 top-3 flex gap-2">
        <span className="rounded bg-black/60 px-2 py-1 text-xs font-semibold text-slate-200">
          <span className={`mr-1 inline-block h-2 w-2 rounded-full ${cameraHealthy ? "bg-red-500" : "bg-slate-500"}`} />
          {cameraHealthy ? "LIVE" : "NO SIGNAL"}
        </span>
        {simulated && (
          <span className="rounded bg-yellow-400/90 px-2 py-1 text-xs font-bold text-slate-950">SIMULATED SCENE</span>
        )}
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 rounded bg-black/60 px-3 py-1.5 text-xs text-slate-200">
        <span>
          <span className="mr-1 inline-block h-0.5 w-4 align-middle bg-red-500" />
          clearance zone
        </span>
        <span>
          <span className="mr-1 inline-block h-0.5 w-4 align-middle bg-amber-400" />
          +6 in watch band
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-4 align-middle border border-white" />
          heater
        </span>
      </div>
    </div>
  );
}
