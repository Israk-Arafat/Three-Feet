import { useEffect, useState } from "react";

/** Annotated MJPEG from the backend. Reconnects automatically if the stream drops. */
export function LiveView({
  cameraHealthy,
  simulated,
  className = "",
}: {
  cameraHealthy: boolean;
  simulated: boolean;
  className?: string;
}) {
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
    <figure className={`screen m-0 ${className}`}>
      {!failed && (
        <img
          key={attempt}
          src={`/stream.mjpg?a=${attempt}`}
          alt="Camera view of the heater with the clearance zone and measured objects drawn on it"
          width={1280}
          height={720}
          className={`h-full w-full object-contain ${cameraHealthy ? "" : "opacity-40"}`}
          onError={() => setFailed(true)}
        />
      )}
      {failed && <p className="p-10 text-lg text-paper-3">Video stream unavailable. Retrying.</p>}
      <div className="pointer-events-none absolute left-3 top-3 flex gap-2">
        <span className="screen__tag">
          <span className="swatch" data-tone={cameraHealthy ? "live" : "info"} aria-hidden="true" />
          {cameraHealthy ? "Live" : "No signal"}
        </span>
        {simulated && <span className="screen__tag tag--amber">Simulated scene</span>}
      </div>
      <figcaption className="screen__tag pointer-events-none absolute bottom-3 left-3 hidden gap-4 md:inline-flex">
        <span className="inline-flex items-center gap-2">
          <span className="legend-line bg-state-warn" aria-hidden="true" />
          Clearance zone
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="legend-line bg-accent-2" aria-hidden="true" />
          Plus 6 in
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="legend-line border border-ink bg-paper-3" aria-hidden="true" />
          Heater
        </span>
      </figcaption>
    </figure>
  );
}
