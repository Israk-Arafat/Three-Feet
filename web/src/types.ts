// Mirrors the backend's Engine._message() and event log (three_feet/engine.py).

export type SafetyState =
  | "CLEAR"
  | "WATCH"
  | "WARN"
  | "VERIFYING"
  | "OFF_CONFIRMED"
  | "OFF_UNCONFIRMED";

export type DegradedFlag = "CAMERA_LOST" | "CALIBRATION_DRIFT" | "API_DOWN" | "PLUG_UNREACHABLE";
export type ObjectStatus = "clear" | "watch" | "violation";
export type Hazard = "high" | "medium" | "low";

export interface ZoneObjectInfo {
  track_id: string;
  label: string;
  source: "sam" | "catchall" | "sim";
  distance_in: number;
  err_in: number;
  required_in: number;
  side: "front" | "back" | "left" | "right" | "inside";
  status: ObjectStatus;
  hazard: Hazard;
  why: string | null;
  assessing: boolean;
  material: string | null;
}

export interface Rule {
  front_in: number;
  back_in: number;
  left_in: number;
  right_in: number;
  source: "manual" | "default";
  quote: string;
  page: number | null;
  heater_model: string;
  approved: boolean;
  approved_at: string | null;
}

export interface Spend {
  total_usd: number;
  last_hour_usd: number;
  budget_usd_per_hour: number;
  calls: Record<string, number>;
}

export interface StateMessage {
  type: "state";
  t: number;
  mode: "live" | "sim" | "replay";
  state: SafetyState;
  banner: string;
  reason: string;
  countdown_s: number | null;
  degraded: DegradedFlag[];
  can_reset: boolean;
  objects: ZoneObjectInfo[];
  rule: Rule;
  power: { reachable: boolean; on: boolean | null; watts: number | null; metering: boolean; history: number[]; plug: string };
  signals: { power_off: boolean | null; indicator_off: boolean | null };
  indicator_configured: boolean;
  camera: { fps: number; healthy: boolean; battery_pct: number | null; wifi_dbm: number | null };
  calibration: {
    configured: boolean;
    method: "markers" | "points" | "heater" | null;
    distance_err_pct: number | null;
    rms_in: number | null;
    drift_px: number | null;
    reference_set: boolean;
  };
  api: {
    ok: boolean;
    sam_latency_s: number | null;
    last_error: string | null;
    concepts: string[];
    spend: Spend;
  };
  open_incident: string | null;
}

export interface EventItem {
  type: "event";
  id: number;
  t: number;
  kind: string;
  text: string;
  data?: Record<string, unknown>;
}

export interface Incident {
  id: string;
  started_t: number;
  ended_t: number | null;
  reason: string;
  outcome: "confirmed" | "unconfirmed" | null;
  report: string | null;
  report_source: "muse-wonder" | "template" | null;
  events: EventItem[];
}

export interface AppConfig {
  mode: "live" | "sim" | "replay";
  demo: boolean;
  source: string;
  plug: string;
  autoplay: boolean | null;
  wonder: boolean;
}

export interface RuleDraft {
  extraction: {
    found: boolean;
    front_in: number | null;
    back_in: number | null;
    sides_in: number | null;
    top_in: number | null;
    quote: string;
    page: number | null;
    heater_model: string;
    confidence: number;
    notes: string;
  };
  rule: Omit<Rule, "approved" | "approved_at"> & { confidence: number };
  simulated: boolean;
  minimum_in: number;
}
