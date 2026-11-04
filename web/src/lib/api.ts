import type { AppConfig, CameraInput, Connections, CyncDevice, Incident, ManualLink, RuleDraft, RuleInput, SetupState, ViewGeometry } from "../types";
import { DEMO, DEMO_MESSAGE, demoIncidents } from "./demo";

/** A failed request. `links` carries manual links the server tried; `devices` the Cync plugs to choose from. */
export class ApiError extends Error {
  constructor(message: string, readonly links: ManualLink[] = [], readonly devices: CyncDevice[] = []) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (DEMO) throw new ApiError(DEMO_MESSAGE); // the Pages build has no server to ask
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
    const d = detail as { message?: string; candidates?: ManualLink[]; devices?: CyncDevice[] };
    if (typeof d?.message === "string") throw new ApiError(d.message, d.candidates ?? [], d.devices ?? []);
    throw new ApiError(JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown, method = "POST") =>
  request<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  connections: () => request<Connections>("/api/connections"),
  testCamera: (body: CameraInput) => post<{ ok: boolean; width: number; height: number }>("/api/connections/camera/test", body),
  setCamera: (body: CameraInput) => post<Connections>("/api/connections/camera", body, "PUT"),
  setPlug: (body: { kind: string; name: string }) => post<Connections>("/api/connections/plug", body, "PUT"),
  cyncLogin: (body: { email: string; password: string }) =>
    post<{ status: "code_required" | "signed_in" }>("/api/connections/cync/login", body),
  cyncCode: (code: string) => post<{ status: "signed_in" }>("/api/connections/cync/code", { code }),
  setup: () => request<SetupState>("/api/setup"),
  viewGeometry: () => request<ViewGeometry>("/api/view/geometry"),
  saveSetupDimensions: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/dimensions", body),
  findHeaterBase: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/auto", body),
  captureSetup: (body: {width_in:number;depth_in:number}) => post<SetupState>("/api/setup/capture", body),
  saveManualSetup: (body: {width_in:number;depth_in:number;job_id:string;corners_px:number[][]}) => post<SetupState>("/api/setup/manual", body),
  config: () => request<AppConfig>("/api/config"),
  incidents: () => (DEMO ? Promise.resolve(demoIncidents()) : request<Incident[]>("/api/incidents")),
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
