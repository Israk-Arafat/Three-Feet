// Mirrors the backend's Engine._message() and event log (three_feet/engine.py).
export interface ViewGeometry {
  configured: boolean; image_size: [number,number] | null; base_corners_px: number[][] | null;
  base_width_in: number | null; base_depth_in: number | null;
}
export interface SetupState {
  width_in: number; depth_in: number; status: "idle"|"running"|"needs_review"|"ready"|"error";
  message: string; job_id: string|null; image_url: string|null; overlay_url: string|null;
  corners_px: number[][]|null; image_size: [number,number]|null;
  quality: {confidence?:number;error_pct?:number}|null; calibrated:boolean; method:string|null;
}

export type SafetyState =
  | "CLEAR"
  | "WATCH"
  | "WARN"
  | "OFF_REQUESTED";

export type DegradedFlag = "CAMERA_LOST" | "CALIBRATION_DRIFT" | "API_DOWN" | "PLUG_UNREACHABLE";
export type ObjectStatus = "clear" | "watch" | "uncertain" | "violation";
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
  source: "manual" | "default" | "user";
  quote: string;
  page: number | null;
  heater_model: string;
  confidence: number;
  approved: boolean;
  approved_at: string | null;
  manual_url: string;
  default_in: number;
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
  time_zone: string;
  watch_margin_in: number;
  mode: "live" | "sim" | "replay";
  state: SafetyState;
  banner: string;
  reason: string;
  countdown_s: number | null;
  degraded: DegradedFlag[];
  can_reset: boolean;
  reset_pending: boolean;
  reset_cancelled: boolean;
  objects: ZoneObjectInfo[];
  rule: Rule;
  power: { reachable: boolean; on: boolean | null; watts: number | null; metering: boolean; history: number[]; plug: string };
  off_pending: boolean;
  off_error: string | null;
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
    health_reason: string | null;
    result_age_s: number | null;
    verification_age_s?: number | null;
    local_tracking?: boolean;
    frame_index: number | null;
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
  outcome: "requested" | "failed" | null;
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

export interface ManualLink {
  url: string;
  title: string;
  official?: boolean;
  error?: string;
}

export interface CyncDevice {
  name: string;
  type: string;
  online: boolean;
  is_plug: boolean;
}

export interface Connections {
  editable: boolean;
  mode: "live" | "sim" | "replay";
  /** null when the plug cannot be reached, so the heater's state is unknown. */
  heater_on: boolean | null;
  camera: {
    kind: "url" | "webcam" | "none" | "other";
    address: string;
    user: string;
    has_password: boolean;
    verify_tls: boolean;
    healthy: boolean;
  };
  plug: { kind: "cync" | "dryrun" | string; name: string; connected: boolean; devices: CyncDevice[]; signed_in: boolean };
}

export interface CameraInput {
  address: string;
  user: string;
  password?: string;
  verify_tls: boolean;
}

/** What the user approves: the four distances plus where they came from. */
export type RuleInput = Omit<Rule, "approved" | "approved_at" | "default_in">;

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
  rule: RuleInput;
  simulated: boolean;
  minimum_in: number;
  /** Set when the manual was found on the web from the heater's name. */
  manual: { url: string; title: string; heater_model: string; read_by: string; tried: ManualLink[] } | null;
}
