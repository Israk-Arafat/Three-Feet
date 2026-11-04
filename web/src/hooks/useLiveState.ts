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
    let resync = false; // the next events message is the server's backlog for a new connection

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/ws`);
      socket.onopen = () => {
        setConnected(true);
        retry = 500;
        resync = true;
      };
      socket.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "state") {
          lastStateAt.current = Date.now();
          setState(msg as StateMessage);
          setStale(false);
        } else if (msg.type === "events") {
          const incoming = msg.events as EventItem[];
          const backlog = resync;
          resync = false;
          setEvents((prev) => {
            // Event ids restart at 1 when the server restarts. If the backlog reuses an id for a different
            // event, or ends below what we have, it is a new server run: start over instead of dropping its
            // events as duplicates.
            const byId = new Map(prev.map((e) => [e.id, e]));
            const prevMax = prev.length ? prev[prev.length - 1].id : 0;
            const nextMax = incoming.length ? incoming[incoming.length - 1].id : 0;
            const restarted =
              nextMax < prevMax || incoming.some((e) => byId.has(e.id) && byId.get(e.id)!.t !== e.t);
            if (backlog && restarted) return incoming.slice(-300);
            const merged = [...prev, ...incoming.filter((e) => !byId.has(e.id))];
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
