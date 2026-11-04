import type { ReactNode } from "react";

export function Panel({
  title,
  note,
  children,
  className = "",
  bodyClassName = "",
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel__head">
        <h2 className="whitespace-nowrap">{title}</h2>
        {note && <span className="panel__note">{note}</span>}
      </header>
      <div className={`panel__body ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export type Tone = "violation" | "watch" | "clear" | "off" | "alert" | "live" | "info" | "ok";

export function Swatch({ tone }: { tone: Tone }) {
  return <span className="swatch" data-tone={tone} aria-hidden="true" />;
}

const STATUS_WORDS = { violation: "In the zone", watch: "Near the zone", uncertain: "Outside, uncertain", clear: "Outside zone" } as const;

/** A measured object's status: a swatch and words, never colour alone. */
export function StatusWord({ status }: { status: keyof typeof STATUS_WORDS }) {
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-medium">
      <Swatch tone={status === "uncertain" ? "watch" : status} />
      {STATUS_WORDS[status]}
    </span>
  );
}

export function HazardTag({ hazard, assessing }: { hazard: string; assessing?: boolean }) {
  const label = { high: "High hazard", medium: "Medium hazard", low: "Low hazard" }[hazard] ?? hazard;
  return (
    <span className={`tag ${hazard === "high" ? "tag--ink" : ""}`}>
      {label}
      {assessing ? ", assessing" : ""}
    </span>
  );
}
