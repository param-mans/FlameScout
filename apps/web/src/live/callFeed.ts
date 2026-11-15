// Every call's status for this web app (/ws/calls): the active calls on connect, then each change. When any
// device taps Call in, every open instance follows that call (App attaches to its incident).
import { useEffect, useRef, useState } from "react";

export interface CallInfo {
  incident_id: string;
  call_id: string;            // c1, c2, ... within the incident
  direction: string;          // "callin" for the web app's Call in
  status: string;             // connected | transcribing | completed | failed | ...
  started_at: number;
}

const ENDED = new Set(["completed", "busy", "no_answer", "rejected", "canceled", "failed"]);
export const isActive = (c: CallInfo) => !ENDED.has(c.status);
const key = (c: CallInfo) => `${c.incident_id}|${c.call_id}`;

/** The newest active call-in (or null). onNewCall runs once per call-in that starts, or is already
 *  running when the page connects. */
export function useCallFeed(onNewCall: (c: CallInfo) => void): CallInfo | null {
  const [calls, setCalls] = useState<Record<string, CallInfo>>({});
  const seen = useRef(new Set<string>());
  const cb = useRef(onNewCall);
  cb.current = onNewCall;

  useEffect(() => {
    let sock: WebSocket | null = null;
    let retry: number | undefined;
    let alive = true;
    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      sock = new WebSocket(`${proto}://${window.location.host}/ws/calls`);
      sock.onmessage = (m) => {
        let ev: { type: string; payload?: { call?: CallInfo } };
        try { ev = JSON.parse(m.data as string); } catch { return; }
        const c = ev.payload?.call;
        if (ev.type !== "call.status" || !c || c.direction !== "callin") return;
        setCalls((prev) => ({ ...prev, [key(c)]: c }));
        if (isActive(c) && !seen.current.has(key(c))) {
          seen.current.add(key(c));
          cb.current(c);
        }
      };
      sock.onclose = () => { if (alive) retry = window.setTimeout(connect, 2000); };
    };
    connect();
    return () => { alive = false; window.clearTimeout(retry); sock?.close(); };
  }, []);

  const active = Object.values(calls).filter(isActive).sort((a, b) => b.started_at - a.started_at);
  return active[0] ?? null;
}
