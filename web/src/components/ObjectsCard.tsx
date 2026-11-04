import { capitalize, measurement } from "../lib/format";
import type { StateMessage } from "../types";
import { HazardTag, Panel, StatusWord } from "./ui";

export function ObjectsCard({ state, className = "" }: { state: StateMessage | null; className?: string }) {
  const simulated = state?.mode === "sim";
  const objects = state?.objects ?? [];
  const worst = objects[0];
  const others = objects.slice(1, 4);
  const note = !worst
    ? undefined
    : worst.source === "catchall"
      ? "Catch-all, not recognized"
      : simulated
        ? "Simulated detection"
        : "SAM 3.1 and Muse Spark";

  return (
    <Panel title="Closest object" note={note} className={className}>
      {!worst ? (
        <p className="py-2 text-lg text-muted">
          {!state ? "Waiting for a fresh camera observation."
            : !state.calibration.configured ? "Map the heater base to start measuring nearby objects."
            : !state.camera.healthy ? "Can’t see the zone. Status unknown."
            : !state.api.ok ? "Object detection is unavailable. Status unknown."
            : "No objects detected near the heater."}
        </p>
      ) : (
        <div className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="truncate text-xl">{capitalize(worst.label)}</h3>
            <StatusWord status={worst.status} />
          </div>
          <div className="plate px-4 py-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="figure num text-5xl leading-none">{worst.distance_in.toFixed(1)}</span>
              <span className="num text-lg">± {worst.err_in.toFixed(1)} in</span>
            </div>
          </div>
          <p className={`text-sm ${worst.status === "uncertain" ? "text-state-warn" : "text-muted"}`}>
            {worst.status === "violation" ? "The detected object overlaps the red zone."
              : worst.status === "uncertain" ? "Outside the red zone, but its estimated range crosses the edge. No shutoff countdown."
              : worst.status === "watch" ? "Just outside the red zone. No shutoff countdown."
              : "Outside the red zone and its watch band. No shutoff countdown."}
          </p>
          {!state?.api.ok && worst.source !== "catchall" && <p className="text-sm text-state-warn">Last model observation; detection is degraded.</p>}
          <p className="text-base">
            From the {worst.side}. The rule needs <b className="font-extrabold">{worst.required_in.toFixed(0)} in</b>.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <HazardTag hazard={worst.hazard} assessing={worst.assessing} />
            {worst.material && <span className="tag">{worst.material}</span>}
          </div>
          {worst.why && <p className="border-l-2 border-ink pl-3 text-base">“{worst.why}”</p>}
        </div>
      )}
      {others.length > 0 && (
        <table className="board mt-4">
          <thead>
            <tr>
              <th>Also seen</th>
              <th className="text-right">Distance</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {others.map((o) => (
              <tr key={o.track_id}>
                <td>{capitalize(o.label)}</td>
                <td className="num text-right">{measurement(o.distance_in, o.err_in)}</td>
                <td>
                  <StatusWord status={o.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
