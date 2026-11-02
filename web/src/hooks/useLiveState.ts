import { useEffect, useRef, useState } from "react";
import type { EventItem, StateMessage } from "../types";

const STALE_AFTER_MS = 2500;

export interface LiveState {
  state: StateMessage | null;
  events: EventItem[];
  connected: boolean;
  /** True when no fresh state has arrived recently: the UI must show UNKNOWN, never a stale CLEAR. */
  stale: boolean;
}

export function useLiveState(): LiveState {
  const [state, setState] = useState<StateMessage | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [connected, setConnected] = useState(false);
  const [stale, setStale] = useState(true);
  const lastStateAt = useRef(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry = 500;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/ws`);
      socket.onopen = () => {
        setConnected(true);
        retry = 500;
      };
      socket.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "state") {
          lastStateAt.current = Date.now();
          setState(msg as StateMessage);
          setStale(false);
        } else if (msg.type === "events") {
          setEvents((prev) => {
            const seen = new Set(prev.map((e) => e.id));
            const merged = [...prev, ...(msg.events as EventItem[]).filter((e) => !seen.has(e.id))];
            return merged.slice(-300);
          });
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) {
          timer = window.setTimeout(connect, retry);
          retry = Math.min(retry * 2, 5000);
        }
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    const staleCheck = window.setInterval(() => {
      setStale(Date.now() - lastStateAt.current > STALE_AFTER_MS);
    }, 500);

    return () => {
      closed = true;
      window.clearTimeout(timer);
      window.clearInterval(staleCheck);
      socket?.close();
    };
  }, []);

  return { state, events, connected, stale };
}
