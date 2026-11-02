import { clock } from "../lib/format";
import type { EventItem } from "../types";
import { Card } from "./ui";

const KIND: Record<string, { dot: string; text: string; label: string }> = {
  warn: { dot: "bg-red-500", text: "text-red-200", label: "WARN" },
  watch: { dot: "bg-amber-400", text: "text-amber-100", label: "WATCH" },
  shutoff: { dot: "bg-orange-500", text: "text-orange-100", label: "POWER CUT" },
  confirmed: { dot: "bg-sky-400", text: "text-sky-100", label: "CONFIRMED" },
  alarm: { dot: "bg-red-500", text: "font-bold text-red-100", label: "ALARM" },
  degraded: { dot: "bg-slate-400", text: "text-slate-100", label: "DEGRADED" },
  fault: { dot: "bg-yellow-400", text: "text-yellow-100", label: "FAULT" },
  reset: { dot: "bg-emerald-400", text: "text-emerald-100", label: "RESET" },
  rule: { dot: "bg-fuchsia-400", text: "text-fuchsia-100", label: "RULE" },
  report: { dot: "bg-fuchsia-400", text: "text-fuchsia-100", label: "REPORT" },
  sim: { dot: "bg-yellow-400", text: "text-yellow-100/80", label: "SIM" },
  setup: { dot: "bg-slate-400", text: "text-slate-200", label: "SETUP" },
  error: { dot: "bg-red-500", text: "text-red-200", label: "ERROR" },
  system: { dot: "bg-slate-500", text: "text-slate-300", label: "SYSTEM" },
  state: { dot: "bg-slate-600", text: "text-slate-400", label: "STATE" },
};

export function Timeline({ events }: { events: EventItem[] }) {
  const items = [...events].reverse().slice(0, 80);
  return (
    <Card title="Timeline" className="flex min-h-0 flex-col">
      <ol className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1 text-sm">
        {items.length === 0 && <li className="text-slate-500">No events yet.</li>}
        {items.map((e) => {
          const k = KIND[e.kind] ?? KIND.system;
          return (
            <li key={e.id} className="flex items-start gap-2">
              <span className="w-20 shrink-0 tabular-nums text-slate-500">{clock(e.t)}</span>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${k.dot}`} />
              <span className="w-24 shrink-0 text-xs font-bold tracking-wide text-slate-400">{k.label}</span>
              <span className={k.text}>{e.text}</span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
