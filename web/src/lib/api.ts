import type { AppConfig, Incident, RuleDraft } from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
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
  config: () => request<AppConfig>("/api/config"),
  incidents: () => request<Incident[]>("/api/incidents"),
  reset: () => post<{ ok: boolean }>("/api/reset"),
  setReference: () => post<{ ok: boolean }>("/api/calibration/reference"),
  extractRule: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<RuleDraft>("/api/rules/extract", { method: "POST", body: form });
  },
  approveRule: (rule: RuleDraft["rule"]) => post<{ ok: boolean }>("/api/rules/approve", rule),
  demo: {
    camera: (on: boolean) => post<{ via: string }>("/api/demo/camera", { on }),
    api: (on: boolean) => post("/api/demo/api", { on }),
    bump: (on: boolean) => post("/api/demo/bump", { on }),
    plug: (action: string) => post("/api/demo/plug", { action }),
    sim: (body: { autoplay?: boolean; object?: string; spot?: string }) =>
      post<{ autoplay: boolean }>("/api/demo/sim", body),
  },
};
