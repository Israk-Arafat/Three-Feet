import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Rule, RuleDraft } from "../types";
import { Icon } from "./Icon";
import { Panel } from "./ui";

function Sides({ r }: { r: Pick<Rule, "front_in" | "back_in" | "left_in" | "right_in"> }) {
  const cells: [string, number][] = [
    ["Front", r.front_in],
    ["Back", r.back_in],
    ["Left", r.left_in],
    ["Right", r.right_in],
  ];
  return (
    <dl className="cells">
      {cells.map(([k, v]) => (
        <div key={k} className="px-2 py-2 sm:px-3">
          <dt className="text-xs font-medium text-muted">{k}</dt>
          <dd className="num m-0 whitespace-nowrap leading-tight">
            <span className="text-xl font-extrabold">{v.toFixed(0)}</span> <span className="text-sm">in</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function RuleDialog({ onClose }: { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [fileName, setFileName] = useState("");

  useEffect(() => {
    dialog.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
    setError(null);
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
    <div className="fixed inset-0 z-(--z-modal) flex items-center justify-center bg-ink/60 p-6" onClick={onClose}>
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="rule-dialog-title"
        tabIndex={-1}
        className="w-full max-w-2xl space-y-4 border-2 border-ink bg-paper-3 p-6 shadow-card outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="rule-dialog-title" className="text-xl">Read the rule from the heater manual</h2>
          <span className="panel__note">Muse Wonder</span>
        </div>
        <input
          ref={input}
          type="file"
          name="manual"
          accept="application/pdf"
          aria-label="Heater manual PDF"
          className="visually-hidden"
          onChange={(e) => e.target.files?.[0] && extract(e.target.files[0])}
        />
        {!draft && (
          <button
            type="button"
            disabled={busy}
            aria-busy={busy}
            onClick={() => input.current?.click()}
            className="btn w-full min-h-28 border-dashed text-base"
          >
            <Icon name="upload" />
            {busy ? `Reading ${fileName}` : "Choose the manual PDF"}
          </button>
        )}
        {error && (
          <p role="alert" className="border-2 border-state-warn bg-paper-3 px-3 py-2 text-state-warn">
            {error}
          </p>
        )}
        {draft && ex && (
          <div className="space-y-4">
            {draft.simulated && (
              <p className="tag tag--amber">Simulated extraction: no API key is set, so this is sample output.</p>
            )}
            <p>
              {ex.found ? (
                <>
                  Found in {fileName}
                  {ex.page ? `, page ${ex.page}` : ""}, {Math.round(ex.confidence * 100)}% confident
                  {ex.heater_model ? `. Heater: ${ex.heater_model}.` : "."}
                </>
              ) : (
                <>No clearance distance in this manual. The {draft.minimum_in} in default applies on every side.</>
              )}
            </p>
            {ex.quote && <blockquote className="m-0 border-l-2 border-ink pl-4 text-lg">“{ex.quote}”</blockquote>}
            <div>
              <p className="mb-2 text-sm text-muted">
                The rule to enforce, the stricter of the manual and the {draft.minimum_in} in minimum:
              </p>
              <Sides r={draft.rule} />
            </div>
            <div className="flex justify-end gap-3">
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="btn btn--primary" onClick={approve} disabled={busy} aria-busy={busy}>
                {busy ? "Saving" : "Approve rule"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function RuleCard({ rule, className = "" }: { rule: Rule | undefined; className?: string }) {
  const [open, setOpen] = useState(false);
  const note = rule?.source === "manual" ? `Muse Wonder${rule.page ? `, manual p.${rule.page}` : ""}` : "Default";
  return (
    <Panel title="Clearance rule" note={rule ? note : undefined} className={className} bodyClassName="flex-1 overflow-y-auto">
      {!rule ? (
        <p className="text-muted">Waiting for the rule.</p>
      ) : (
        <div className="space-y-2">
          <Sides r={rule} />
          {rule.source === "manual" ? (
            rule.quote && (
              <p className="truncate text-sm" title={rule.quote}>
                “{rule.quote}”
              </p>
            )
          ) : (
            <p className="text-sm text-muted">36 in on every side until a manual is read.</p>
          )}
          <div className="flex items-center justify-between gap-3">
            <span className={`text-sm font-medium ${rule.approved ? "text-ink" : "text-muted"}`}>
              {rule.approved
                ? `Approved ${rule.approved_at ? new Date(rule.approved_at).toLocaleString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }) : ""}`
                : "Not reviewed yet"}
            </span>
            <button type="button" className="btn btn--sm" onClick={() => setOpen(true)}>
              Read manual
            </button>
          </div>
        </div>
      )}
      {open && <RuleDialog onClose={() => setOpen(false)} />}
    </Panel>
  );
}
