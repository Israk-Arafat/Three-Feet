import { useDemoSnapshot } from "../lib/demo";
import type { LiveState } from "./useLiveState";

/** The same shape as `useLiveState`, fed by the recorded run instead of a server. */
export function useDemoState(): LiveState {
  const { ready, state, events } = useDemoSnapshot();
  return { state, events, connected: ready, stale: !ready || !state };
}
