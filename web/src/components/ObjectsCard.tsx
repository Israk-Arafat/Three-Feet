import type { StateMessage, ZoneObjectInfo } from "../types";
import { Card, HazardPill, ModelChip, StatusPill } from "./ui";

function sourceChip(o: ZoneObjectInfo, simulated: boolean) {
  if (o.source === "catchall") return <ModelChip name="catch-all" />;
  return simulated ? <ModelChip name="SIMULATED" /> : <ModelChip name="SAM 3.1" />;
}

export function ObjectsCard({ state }: { state: StateMessage | null }) {
  const simulated = state?.mode === "sim";
  const objects = state?.objects ?? [];
  const worst = objects[0];
  const others = objects.slice(1, 4);

  return (
    <Card title="Closest object" chip={worst ? sourceChip(worst, simulated) : undefined}>
      {!worst ? (
        <p className="py-4 text-lg text-slate-400">
          {state?.camera.healthy === false ? "Can't see the zone — status unknown." : "Nothing near the heater."}
        </p>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-2xl font-bold capitalize">{worst.label}</div>
            <StatusPill status={worst.status} />
          </div>
          <div className="flex items-baseline gap-3">
            <span className="text-5xl font-black tabular-nums">{worst.distance_in.toFixed(1)}</span>
            <span className="text-xl text-slate-300">in ± {worst.err_in.toFixed(1)}</span>
          </div>
          <div className="text-base text-slate-300">
            from the heater's <b>{worst.side}</b> · rule requires <b>{worst.required_in.toFixed(0)} in</b>
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <HazardPill hazard={worst.hazard} assessing={worst.assessing} />
            {worst.source !== "catchall" && <ModelChip name={simulated ? "SIMULATED" : "Muse Spark"} />}
          </div>
          {worst.why && (
            <p className="rounded-lg bg-slate-800/70 px-3 py-2 text-base italic text-slate-200">
              “{worst.why}”{worst.material ? <span className="not-italic text-slate-400"> — {worst.material}</span> : null}
            </p>
          )}
        </div>
      )}
      {others.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-slate-800 pt-2 text-sm">
          {others.map((o) => (
            <li key={o.track_id} className="flex items-center justify-between gap-2">
              <span className="truncate capitalize text-slate-300">{o.label}</span>
              <span className="flex items-center gap-2 tabular-nums">
                {o.distance_in.toFixed(1)} in
                <StatusPill status={o.status} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
