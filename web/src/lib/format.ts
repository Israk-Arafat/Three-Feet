export const inches = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : `${v.toFixed(digits)} in`;

export const clock = (t: number, timeZone = "America/New_York") =>
  new Date(t * 1000).toLocaleTimeString([], { timeZone, hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });

export const measurement = (distance: number, error: number) => `${distance.toFixed(1)} ± ${error.toFixed(1)} in`;

export const watts = (w: number | null | undefined) =>
  w === null || w === undefined ? "—" : `${Math.round(w).toLocaleString()} W`;

export const usd = (v: number) => `$${v.toFixed(v < 1 ? 3 : 2)}`;

export const capitalize = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const humanFlag = (flag: string) =>
  ({
    CAMERA_LOST: "camera feed lost",
    CALIBRATION_DRIFT: "camera moved (calibration drift)",
    API_DOWN: "object detection not ready",
    PLUG_UNREACHABLE: "smart plug unreachable",
  })[flag] ?? flag.toLowerCase().replace(/_/g, " ");

/** Rounded for speech: "14 inches" sounds better than "14.2 in". */
export const spokenInches = (v: number) => `${Math.max(0, Math.round(v))} inches`;
