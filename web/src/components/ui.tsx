import type { ReactNode } from "react";

export function Card({
  title,
  chip,
  children,
  className = "",
}: {
  title: string;
  chip?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/80 p-4 ${className}`}>
      <header className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400">{title}</h2>
        {chip}
      </header>
      {children}
    </section>
  );
}

const MODEL_STYLES: Record<string, string> = {
  "SAM 3.1": "bg-violet-500/20 text-violet-200 ring-violet-400/40",
  "Muse Spark": "bg-cyan-500/20 text-cyan-200 ring-cyan-400/40",
  "Muse Wonder": "bg-fuchsia-500/20 text-fuchsia-200 ring-fuchsia-400/40",
  "catch-all": "bg-slate-500/20 text-slate-200 ring-slate-400/40",
  SIMULATED: "bg-yellow-500/20 text-yellow-200 ring-yellow-400/50",
  template: "bg-slate-500/20 text-slate-300 ring-slate-400/40",
};

/** Attribution chip so viewers can see which Meta model produced each output. */
export function ModelChip({ name }: { name: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${
        MODEL_STYLES[name] ?? MODEL_STYLES["catch-all"]
      }`}
    >
      {name}
    </span>
  );
}

const STATUS_STYLES = {
  violation: "bg-red-600 text-white",
  watch: "bg-amber-400 text-slate-950",
  clear: "bg-emerald-600 text-white",
} as const;

export function StatusPill({ status }: { status: keyof typeof STATUS_STYLES }) {
  const text = { violation: "IN ZONE", watch: "NEAR", clear: "CLEAR" }[status];
  return <span className={`rounded px-2 py-0.5 text-xs font-bold ${STATUS_STYLES[status]}`}>{text}</span>;
}

export function HazardPill({ hazard, assessing }: { hazard: string; assessing?: boolean }) {
  const style =
    hazard === "high"
      ? "bg-red-500/20 text-red-200 ring-red-400/50"
      : hazard === "medium"
        ? "bg-amber-500/20 text-amber-200 ring-amber-400/50"
        : "bg-emerald-500/20 text-emerald-200 ring-emerald-400/50";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ring-1 ${style}`}>
      {hazard} hazard{assessing ? " (assessing…)" : ""}
    </span>
  );
}

export function Check({ value, label }: { value: boolean | null; label: string }) {
  const icon = value === true ? "✓" : value === false ? "✗" : "–";
  const color = value === true ? "text-emerald-400" : value === false ? "text-red-400" : "text-slate-500";
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className={`w-4 text-center text-lg font-bold ${color}`}>{icon}</span>
      <span className={value === null ? "text-slate-500" : "text-slate-200"}>{label}</span>
    </div>
  );
}
