import type { AppConfig, Incident, ManualLink, RuleDraft, RuleInput, SetupState, ViewGeometry } from "../types";

/** A failed request. `links` carries manual links the server tried, when a manual search fails. */
export class ApiError extends Error {
  constructor(message: string, readonly links: ManualLink[] = []) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* not JSON */
    }
    if (typeof detail === "string") throw new ApiError(detail);
    const d = detail as { message?: string; candidates?: ManualLink[] };
    if (typeof d?.message === "string") throw new ApiError(d.message, d.candidates ?? []);
    throw new ApiError(JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  setup: () => request<SetupState>("/api/setup"),
  viewGeometry: () => request<ViewGeometry>("/api/view/geometry"),
  saveSetupDimensions: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/dimensions", body),
  findHeaterBase: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/auto", body),
  captureSetup: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/capture", body),
  saveManualSetup: (body: {width_in:number;depth_in:number;job_id:string;corners_px:number[][]}) => post<SetupState>("/api/setup/manual", body),
  config: () => request<AppConfig>("/api/config"),
  incidents: () => request<Incident[]>("/api/incidents"),
  reset: () => post<{ ok: boolean }>("/api/reset"),
  setReference: () => post<{ ok: boolean }>("/api/calibration/reference"),
  extractRule: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<RuleDraft>("/api/rules/extract", { method: "POST", body: form });
  },
  findManual: (heater_name: string) => post<RuleDraft>("/api/rules/find", { heater_name }),
  approveRule: (rule: RuleInput) => post<{ ok: boolean }>("/api/rules/approve", rule),
  demo: {
    camera: (on: boolean) => post<{ via: string }>("/api/demo/camera", { on }),
    api: (on: boolean) => post("/api/demo/api", { on }),
    bump: (on: boolean) => post("/api/demo/bump", { on }),
    plug: (action: string) => post("/api/demo/plug", { action }),
    sim: (body: { autoplay?: boolean; object?: string; spot?: string }) =>
      post<{ autoplay: boolean }>("/api/demo/sim", body),
  },
};
