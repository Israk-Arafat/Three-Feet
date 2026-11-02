import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { Rule, RuleDraft } from "../types";
import { Card, ModelChip } from "./ui";

function Sides({ r }: { r: Pick<Rule, "front_in" | "back_in" | "left_in" | "right_in"> }) {
  const cells: [string, number][] = [
    ["Front", r.front_in],
    ["Back", r.back_in],
    ["Left", r.left_in],
    ["Right", r.right_in],
  ];
  return (
    <div className="grid grid-cols-4 gap-2 text-center">
      {cells.map(([k, v]) => (
        <div key={k} className="rounded-lg bg-slate-800/70 py-1.5">
          <div className="text-xs uppercase text-slate-400">{k}</div>
          <div className="text-xl font-bold tabular-nums">{v.toFixed(0)}″</div>
        </div>
      ))}
    </div>
  );
}

function RuleDialog({ onClose }: { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [fileName, setFileName] = useState("");

  const extract = async (file: File) => {
    setBusy(true);
    setError(null);
    setFileName(file.name);
    try {
      setDraft(await api.extractRule(file));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      await api.approveRule(draft.rule);
      onClose();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const ex = draft?.extraction;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={onClose}>
      <div
        className="w-full max-w-2xl space-y-4 rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-2xl font-bold">Read clearance rule from the heater manual</h3>
          <ModelChip name="Muse Wonder" />
        </div>
        <input
          ref={input}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && extract(e.target.files[0])}
        />
        {!draft && (
          <button
            disabled={busy}
            onClick={() => input.current?.click()}
            className="w-full rounded-xl border-2 border-dashed border-slate-600 py-10 text-lg text-slate-300 hover:border-slate-400 disabled:opacity-60"
          >
            {busy ? `Muse Wonder is reading ${fileName}…` : "Choose the manual PDF"}
          </button>
        )}
        {error && <p className="rounded bg-red-900/60 px-3 py-2 text-red-100">{error}</p>}
        {draft && ex && (
          <div className="space-y-3">
            {draft.simulated && (
              <p className="rounded bg-yellow-400/90 px-3 py-1.5 text-sm font-bold text-slate-950">
                SIMULATED extraction — no API key configured; this is sample output.
              </p>
            )}
            <p className="text-slate-300">
              {ex.found ? (
                <>
                  Found in <b>{fileName}</b>
                  {ex.page ? (
                    <>
                      , <b>page {ex.page}</b>
                    </>
                  ) : null}{" "}
                  ({Math.round(ex.confidence * 100)}% confidence){ex.heater_model ? ` · ${ex.heater_model}` : ""}
                </>
              ) : (
                <>No clearance distance was found — the {draft.minimum_in}″ default will be used on every side.</>
              )}
            </p>
            {ex.quote && (
              <blockquote className="border-l-4 border-fuchsia-400 bg-slate-800/70 px-4 py-2 text-lg italic">
                “{ex.quote}”
              </blockquote>
            )}
            <div>
              <div className="mb-1 text-sm text-slate-400">
                Rule to enforce (the stricter of the manual and a {draft.minimum_in}″ minimum):
              </div>
              <Sides r={draft.rule} />
            </div>
            <div className="flex justify-end gap-3 pt-2">
              <button onClick={onClose} className="rounded-lg px-4 py-2 text-slate-300 hover:bg-slate-800">
                Cancel
              </button>
              <button
                onClick={approve}
                disabled={busy}
                className="rounded-lg bg-emerald-600 px-5 py-2 text-lg font-bold hover:bg-emerald-500 disabled:opacity-60"
              >
                Approve rule
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function RuleCard({ rule }: { rule: Rule | undefined }) {
  const [open, setOpen] = useState(false);
  return (
    <Card title="Clearance rule" chip={rule?.source === "manual" ? <ModelChip name="Muse Wonder" /> : undefined}>
      {!rule ? (
        <p className="text-slate-400">—</p>
      ) : (
        <div className="space-y-2">
          <Sides r={rule} />
          {rule.source === "manual" ? (
            <>
              <div className="text-sm text-slate-300">
                {rule.heater_model || "Heater"} manual{rule.page ? `, p.${rule.page}` : ""}
              </div>
              {rule.quote && <p className="line-clamp-3 text-sm italic text-slate-300">“{rule.quote}”</p>}
            </>
          ) : (
            <div className="text-sm text-slate-400">Default 36″ on every side — no manual loaded yet.</div>
          )}
          <div className="flex items-center justify-between pt-1">
            <span className={`text-sm font-semibold ${rule.approved ? "text-emerald-400" : "text-amber-300"}`}>
              {rule.approved
                ? `✓ Approved${rule.approved_at ? ` ${new Date(rule.approved_at).toLocaleString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" })}` : ""}`
                : "Not reviewed"}
            </span>
            <button onClick={() => setOpen(true)} className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm font-semibold hover:bg-slate-700">
              Load from manual…
            </button>
          </div>
        </div>
      )}
      {open && <RuleDialog onClose={() => setOpen(false)} />}
    </Card>
  );
}
