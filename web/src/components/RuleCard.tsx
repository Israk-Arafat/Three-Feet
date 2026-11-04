import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../lib/api";
import type { ManualLink, Rule, RuleDraft, RuleInput } from "../types";
import { Icon } from "./Icon";
import { Panel } from "./ui";

const SIDES = ["front", "back", "left", "right"] as const;
type Side = (typeof SIDES)[number];
type Distances = Record<Side, string>;
const LABEL: Record<Side, string> = { front: "Front", back: "Back", left: "Left", right: "Right" };
const MAX_IN = 240;

const fmt = (n: number) => String(Math.round(n * 10) / 10);
const toDistances = (r: Pick<Rule, `${Side}_in`>): Distances => ({
  front: fmt(r.front_in),
  back: fmt(r.back_in),
  left: fmt(r.left_in),
  right: fmt(r.right_in),
});

const valid = (v: string) => v.trim() !== "" && Number(v) > 0 && Number(v) <= MAX_IN;

function parse(d: Distances): Record<Side, number> | null {
  if (!SIDES.every((s) => valid(d[s]))) return null;
  return { front: Number(d.front), back: Number(d.back), left: Number(d.left), right: Number(d.right) };
}

const toInput = (r: Rule): RuleInput => ({
  front_in: r.front_in,
  back_in: r.back_in,
  left_in: r.left_in,
  right_in: r.right_in,
  source: r.source,
  quote: r.quote,
  page: r.page,
  heater_model: r.heater_model,
  confidence: r.confidence,
  manual_url: r.manual_url,
});

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

const list = (words: string[]) =>
  words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;

function Sides({ r }: { r: Pick<Rule, "front_in" | "back_in" | "left_in" | "right_in"> }) {
  return (
    <dl className="cells">
      {SIDES.map((s) => (
        <div key={s} className="px-2 py-2 sm:px-3">
          <dt className="text-xs font-medium text-muted">{LABEL[s]}</dt>
          <dd className="num m-0 whitespace-nowrap leading-tight">
            <span className="text-xl font-extrabold">{fmt(r[`${s}_in`])}</span> <span className="text-sm">in</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Links({ links }: { links: ManualLink[] }) {
  return (
    <ul className="space-y-1 pl-4 text-sm">
      {links.map((l) => (
        <li key={l.url}>
          <a href={l.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
            {l.title || host(l.url)}
          </a>
          {l.error && <span className="text-muted"> ({l.error})</span>}
        </li>
      ))}
    </ul>
  );
}

type Busy = null | "search" | "upload" | "save";

function RuleDialog({ rule, mode, onClose }: { rule: Rule; mode: "find" | "edit"; onClose: () => void }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [stage, setStage] = useState<"source" | "review">(mode === "edit" ? "review" : "source");
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [values, setValues] = useState<Distances>(() => toDistances(rule));
  const [heaterName, setHeaterName] = useState(rule.heater_model.replace(/^SIMULATED\s+/i, ""));
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<{ message: string; links: ManualLink[] } | null>(null);

  useEffect(() => {
    if (!dialog.current?.open) dialog.current?.showModal();
  }, []);

  useEffect(() => {
    if (busy !== "search") return;
    setElapsed(0);
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [busy]);

  const base: RuleInput = draft ? draft.rule : toInput(rule);
  const parsed = parse(values);
  const changed = !parsed || SIDES.some((s) => parsed[s] !== base[`${s}_in`]);
  const below = parsed ? SIDES.filter((s) => parsed[s] < rule.default_in) : [];
  const allSame = SIDES.every((s) => values[s] === values.front) ? values.front : "";

  const ex = draft?.extraction;
  const manualDistances: Distances | null =
    ex?.found
      ? {
          front: ex.front_in != null ? fmt(ex.front_in) : values.front,
          back: ex.back_in != null ? fmt(ex.back_in) : values.back,
          left: ex.sides_in != null ? fmt(ex.sides_in) : values.left,
          right: ex.sides_in != null ? fmt(ex.sides_in) : values.right,
        }
      : null;
  const canUseManual = manualDistances && SIDES.some((s) => manualDistances[s] !== values[s]);

  const run = async (kind: Busy, fetchDraft: () => Promise<RuleDraft>) => {
    setBusy(kind);
    setError(null);
    try {
      const d = await fetchDraft();
      setDraft(d);
      setValues(toDistances(d.rule));
      setStage("review");
    } catch (e) {
      setError({ message: (e as Error).message, links: e instanceof ApiError ? e.links : [] });
    } finally {
      setBusy(null);
    }
  };

  const find = (e: FormEvent) => {
    e.preventDefault();
    const name = heaterName.trim();
    if (name.length >= 2) run("search", () => api.findManual(name));
  };

  const upload = (file: File) => {
    setFileName(file.name);
    run("upload", () => api.extractRule(file));
  };

  const save = async () => {
    if (!parsed) return;
    setBusy("save");
    setError(null);
    try {
      await api.approveRule({
        ...base,
        front_in: parsed.front,
        back_in: parsed.back,
        left_in: parsed.left,
        right_in: parsed.right,
        source: changed ? "user" : base.source,
      });
      onClose();
    } catch (e) {
      setError({ message: (e as Error).message, links: [] });
      setBusy(null);
    }
  };

  const errorBox = error && (
    <div role="alert" className="space-y-2 border-2 border-state-warn bg-paper-3 px-3 py-2">
      <p className="text-state-warn">{error.message}</p>
      {error.links.length > 0 && <Links links={error.links} />}
    </div>
  );

  return (
    <dialog
      ref={dialog}
      className="rule-dialog"
      aria-labelledby="rule-dialog-title"
      onClose={onClose}
      onPointerDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="space-y-4 p-6">
        {stage === "source" ? (
          <>
            <div className="flex items-baseline justify-between gap-4">
              <h2 id="rule-dialog-title" className="text-xl">Find your heater's clearance rule</h2>
              <span className="panel__note">Muse Spark + Muse Wonder</span>
            </div>
            <form onSubmit={find} className="space-y-2">
              <label htmlFor="heater-name" className="block text-sm font-extrabold">
                Heater brand and model
              </label>
              <div className="flex flex-wrap gap-2">
                <input
                  id="heater-name"
                  name="heater-name"
                  autoComplete="off"
                  maxLength={120}
                  placeholder="For example, Lasko 754200"
                  value={heaterName}
                  disabled={busy !== null}
                  onChange={(e) => setHeaterName(e.target.value)}
                  className="min-w-0 flex-1 border-2 border-ink bg-paper px-3 py-2 text-lg"
                />
                <button
                  type="submit"
                  className="btn btn--primary"
                  disabled={busy !== null || heaterName.trim().length < 2}
                  aria-busy={busy === "search"}
                >
                  {busy === "search" ? "Searching" : "Find manual"}
                </button>
              </div>
              <p className="text-sm text-muted">
                The model number is usually on a label on the back or bottom. Muse Spark searches the web for the
                owner's manual and reads its clearance distances. You review them before anything changes.
              </p>
              {busy === "search" && (
                <p role="status" className="text-sm font-medium">
                  Searching the web and reading the manual, {elapsed} s. This usually takes a minute or two.
                </p>
              )}
            </form>
            <input
              ref={fileInput}
              type="file"
              name="manual"
              accept="application/pdf"
              aria-label="Heater manual PDF"
              className="visually-hidden"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
            <button
              type="button"
              disabled={busy !== null}
              aria-busy={busy === "upload"}
              onClick={() => fileInput.current?.click()}
              className="btn w-full min-h-20 border-dashed"
            >
              <Icon name="upload" />
              {busy === "upload" ? `Reading ${fileName}` : "Or upload the manual PDF"}
            </button>
            {errorBox}
            <div className="flex justify-end gap-3">
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="btn" disabled={busy !== null} onClick={() => setStage("review")}>
                Enter distances myself
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-4">
              <h2 id="rule-dialog-title" className="text-xl">
                {draft ? "Review the clearance rule" : "Set the clearance distances"}
              </h2>
              {draft && <span className="panel__note">{draft.manual?.read_by ?? "Muse Wonder"}</span>}
            </div>

            {draft && ex ? (
              <div className="space-y-3">
                {draft.simulated && (
                  <p className="tag tag--amber">Simulated: no API key is set, so this is sample output.</p>
                )}
                <p>
                  {ex.found ? "Found in " : "No clearance distance in "}
                  {draft.manual ? (
                    draft.manual.url ? (
                      <a href={draft.manual.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
                        the {draft.manual.heater_model} manual ({host(draft.manual.url)})
                      </a>
                    ) : (
                      `the ${draft.manual.heater_model} manual`
                    )
                  ) : (
                    fileName
                  )}
                  {ex.found && ex.page ? `, page ${ex.page}` : ""}.
                  {ex.heater_model && ` The manual is for: ${ex.heater_model}.`}
                  {!ex.found && ` The ${draft.minimum_in} in default fills every side.`}
                </p>
                {ex.quote && <blockquote className="border-l-2 border-ink pl-4 text-lg">“{ex.quote}”</blockquote>}
                {draft.manual && draft.manual.tried.length > 0 && (
                  <details className="text-sm">
                    <summary className="cursor-pointer text-muted">Other links tried</summary>
                    <Links links={draft.manual.tried} />
                  </details>
                )}
                <p className="text-sm text-muted">
                  Filled in with the stricter of the manual and the {draft.minimum_in} in default. Adjust any side
                  before you approve.
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted">
                {rule.source !== "default" && rule.quote
                  ? `Current rule${rule.page ? ` from manual page ${rule.page}` : ""}: “${rule.quote}” `
                  : ""}
                Distance from each side of the heater, in inches.
              </p>
            )}

            <fieldset className="space-y-3">
              <legend className="visually-hidden">Clearance on each side, in inches</legend>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {SIDES.map((s) => (
                  <label key={s} className="flex flex-col gap-1 text-sm font-extrabold">
                    {LABEL[s]}
                    <span className="flex items-center gap-2">
                      <input
                        type="number"
                        inputMode="decimal"
                        min={1}
                        max={MAX_IN}
                        step={1}
                        value={values[s]}
                        disabled={busy !== null}
                        aria-invalid={!valid(values[s])}
                        onChange={(e) => setValues((v) => ({ ...v, [s]: e.target.value }))}
                        className="num w-full min-w-0 border-2 border-ink bg-paper px-3 py-2 text-lg"
                      />
                      <span className="font-normal">in</span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-sm font-extrabold">
                  All sides
                  <span className="flex items-center gap-2">
                    <input
                      type="number"
                      inputMode="decimal"
                      min={1}
                      max={MAX_IN}
                      step={1}
                      value={allSame}
                      placeholder="—"
                      disabled={busy !== null}
                      onChange={(e) => {
                        const v = e.target.value;
                        setValues({ front: v, back: v, left: v, right: v });
                      }}
                      className="num w-28 border-2 border-ink bg-paper px-3 py-2 text-lg"
                    />
                    <span className="font-normal">in</span>
                  </span>
                </label>
                {canUseManual && (
                  <button type="button" className="btn btn--sm" onClick={() => setValues(manualDistances)}>
                    Use the manual's exact distances
                  </button>
                )}
              </div>
            </fieldset>

            {!parsed && (
              <p role="alert" className="text-sm text-state-warn">
                Enter a distance above 0 and up to {MAX_IN} in for every side.
              </p>
            )}
            {below.length > 0 && (
              <p role="status" className="border-2 border-state-warn bg-paper-3 px-3 py-2 text-sm">
                <strong>Below the {rule.default_in} in default</strong> on the {list(below)}. Only go lower if the
                heater's manual allows it; objects closer than this will not trigger a shutoff.
              </p>
            )}
            {errorBox}

            <div className="flex flex-wrap justify-end gap-3">
              <button type="button" className="btn mr-auto" disabled={busy !== null} onClick={() => setStage("source")}>
                {draft ? "Try another manual" : "Find the manual instead"}
              </button>
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={save}
                disabled={busy !== null || !parsed}
                aria-busy={busy === "save"}
              >
                {busy === "save" ? "Saving" : draft && !changed ? "Approve rule" : "Save rule"}
              </button>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}

export function RuleCard({ rule, timeZone = "America/New_York", className = "" }: { rule: Rule | undefined; timeZone?: string; className?: string }) {
  const [open, setOpen] = useState<null | "find" | "edit">(null);
  const note = !rule
    ? undefined
    : rule.source === "manual"
      ? `Manual${rule.page ? `, p.${rule.page}` : ""}`
      : rule.source === "user"
        ? "Set by you"
        : "Default";
  const below = rule ? SIDES.filter((s) => rule[`${s}_in`] < rule.default_in) : [];
  return (
    <Panel title="Clearance rule" note={note} className={className} bodyClassName="flex-1 overflow-y-auto">
      {!rule ? (
        <p className="text-muted">Waiting for the rule.</p>
      ) : (
        <div className="space-y-2">
          <Sides r={rule} />
          <p className="text-sm text-muted">Active default: {rule.default_in} in. {rule.source === "user" ? "Custom distances selected by you." : rule.source === "manual" ? "Distances reviewed from the manual." : "Default distances apply."}</p>
          {rule.manual_url && <a href={rule.manual_url} target="_blank" rel="noreferrer" className="text-sm underline">View source manual{rule.page ? `, page ${rule.page}` : ""}</a>}
          {rule.source === "default" ? (
            <p className="text-sm text-muted">
              {rule.default_in} in on every side until you find the manual or set your own.
            </p>
          ) : (
            rule.quote && (
              <p className="truncate text-sm" title={rule.quote}>
                {rule.source === "user" && <span className="text-muted">Manual: </span>}“{rule.quote}”
              </p>
            )
          )}
          {below.length > 0 && (
            <p className="text-sm font-medium text-state-warn">
              Below the {rule.default_in} in default on the {list(below)}.
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className={`text-sm font-medium ${rule.approved ? "text-ink" : "text-muted"}`}>
              {rule.approved
                ? `Approved ${rule.approved_at ? new Date(rule.approved_at).toLocaleString([], { timeZone, timeZoneName: "short", hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }) : ""}`
                : "Not reviewed yet"}
            </span>
            <span className="flex gap-2">
              <button type="button" className="btn btn--sm" onClick={() => setOpen("edit")}>
                Edit
              </button>
              <button type="button" className="btn btn--sm" onClick={() => setOpen("find")}>
                Find manual
              </button>
            </span>
          </div>
        </div>
      )}
      {open && rule && <RuleDialog rule={rule} mode={open} onClose={() => setOpen(null)} />}
    </Panel>
  );
}
