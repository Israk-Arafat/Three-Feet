import type { StateMessage } from "../types";

/** Presentation only: never cap or alter the backend's uncertainty or zone decision. */
export function hasApproximateScale(state: StateMessage | null): boolean {
  return !!state && (state.calibration.distance_err_pct ?? 0) >= 50;
}
