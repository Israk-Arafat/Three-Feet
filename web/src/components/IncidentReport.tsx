import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { clock } from "../lib/format";
import type { EventItem, Incident } from "../types";
import { Card, ModelChip } from "./ui";

export function IncidentReport({ events, openIncident }: { events: EventItem[]; openIncident: string | null }) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const lastReportEvent = [...events].reverse().find((e) => e.kind === "report")?.id ?? 0;

  useEffect(() => {
    if (!lastReportEvent) return;
    api
      .incidents()
      .then((list) => setIncident(list.find((i) => i.report) ?? null))
      .catch(() => undefined);
  }, [lastReportEvent]);

  const chip = incident?.report_source === "muse-wonder" ? <ModelChip name="Muse Wonder" /> : incident ? <ModelChip name="template" /> : undefined;

  return (
    <Card title="Incident report" chip={chip} className="flex min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {openIncident && <p className="mb-2 animate-pulse text-orange-200">Incident in progress — verifying shutoff…</p>}
        {!incident ? (
          !openIncident && <p className="text-slate-500">No shutoffs yet. After each one, Muse Wonder explains what happened.</p>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <span>{clock(incident.started_t)}</span>
              <span
                className={`rounded px-2 py-0.5 text-xs font-bold ${
                  incident.outcome === "confirmed" ? "bg-sky-700 text-white" : "bg-red-600 text-white"
                }`}
              >
                {incident.outcome === "confirmed" ? "SHUTOFF CONFIRMED" : "NOT CONFIRMED"}
              </span>
            </div>
            <p className="whitespace-pre-line text-base leading-relaxed text-slate-100">{incident.report}</p>
          </div>
        )}
      </div>
    </Card>
  );
}
