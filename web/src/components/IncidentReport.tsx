import { memo, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { clock } from "../lib/format";
import type { EventItem, Incident } from "../types";

function IncidentReportImpl({
  events,
  openIncident,
  className = "",
}: {
  events: EventItem[];
  openIncident: string | null;
  className?: string;
}) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const lastReportEvent = useMemo(() => events.filter((e) => e.kind === "report").at(-1)?.id ?? 0, [events]);

  // Load on mount (the report event may be older than the event backlog), after each new report, and when
  // an open incident closes.
  useEffect(() => {
    let active = true;
    api
      .incidents()
      .then((list) => active && setIncident(list.find((i) => i.report) ?? null))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [lastReportEvent, openIncident]);

  const note = !incident ? undefined : incident.report_source === "muse-wonder" ? "Written by Muse Wonder" : "Written from the log";

  return (
    <section className={`panel ${className}`}>
      <header className="panel__head">
        <h2>Last incident</h2>
        {note && <span className="panel__note">{note}</span>}
      </header>
      <div className="panel__body flex-1 space-y-3 overflow-y-auto">
        {openIncident && <p className="font-medium">An incident is open. Checking the shutoff now.</p>}
        {!incident ? (
          !openIncident && <p className="text-muted">No shutoffs yet. After each one, Muse Wonder writes up what happened.</p>
        ) : (
          <>
            <div className="flex items-center gap-3 text-sm">
              <span className="num text-muted">{clock(incident.started_t)}</span>
              <span className={`tag ${incident.outcome === "confirmed" ? "tag--ink" : "tag--amber"}`}>
                {incident.outcome === "confirmed" ? "Shutoff confirmed" : "Not confirmed"}
              </span>
            </div>
            <p className="whitespace-pre-line text-base leading-relaxed">{incident.report}</p>
          </>
        )}
      </div>
    </section>
  );
}

export const IncidentReport = memo(IncidentReportImpl);
