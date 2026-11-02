import { clock } from "../lib/format";
import type { EventItem } from "../types";
import { Swatch, type Tone } from "./ui";

const KIND: Record<string, { tone: Tone; label: string }> = {
  warn: { tone: "violation", label: "Too close" },
  watch: { tone: "watch", label: "Near" },
  shutoff: { tone: "off", label: "Power cut" },
  confirmed: { tone: "off", label: "Confirmed" },
  alarm: { tone: "alert", label: "Alarm" },
  degraded: { tone: "alert", label: "Degraded" },
  fault: { tone: "watch", label: "Fault test" },
  reset: { tone: "clear", label: "Reset" },
  rule: { tone: "live", label: "Rule" },
  report: { tone: "live", label: "Report" },
  sim: { tone: "info", label: "Simulator" },
  setup: { tone: "info", label: "Setup" },
  error: { tone: "alert", label: "Error" },
  system: { tone: "info", label: "System" },
  state: { tone: "info", label: "State" },
};

/** The event board: newest first, one row per event, like a departure board. */
export function Timeline({ events, className = "" }: { events: EventItem[]; className?: string }) {
  const items = [...events].reverse().slice(0, 80);
  return (
    <section className={`panel ${className}`}>
      <header className="panel__head">
        <h2>Event log</h2>
        <span className="panel__note">{events.length} events</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {items.length === 0 ? (
          <p className="p-4 text-muted">No events yet.</p>
        ) : (
          <table className="board board--sticky">
            <thead>
              <tr>
                <th className="w-28">Time</th>
                <th className="w-36">Event</th>
                <th>What happened</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e) => {
                const k = KIND[e.kind] ?? KIND.system;
                return (
                  <tr key={e.id} className={e.kind === "state" ? "text-muted" : ""}>
                    <td className="num whitespace-nowrap text-muted">{clock(e.t)}</td>
                    <td className="whitespace-nowrap font-medium">
                      <span className="inline-flex items-center gap-2">
                        <Swatch tone={k.tone} />
                        {k.label}
                      </span>
                    </td>
                    <td>{e.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
