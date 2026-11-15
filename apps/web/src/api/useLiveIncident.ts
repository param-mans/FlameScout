import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiEvent, Brief, BuildingCandidate, CallSegment, CopilotSuggestion, ExtractedFact, IncidentRecord, Progress } from "../types/schemas";
import { api, ApiError, type ScriptInfo } from "./client";
import { overlayFromEvent } from "../house/useIncidentSocket";
import { INCIDENT_OFF, publishLiveFeed } from "../house/liveFeed";

// Live data for the Dispatcher console and the MDT: REST for actions and hydration, one WebSocket per incident
// (/ws/incidents/{id}) for everything that changes. Falls back to mock data (see App) when the API is down.

export type Mode = "checking" | "live" | "mock";
export type Conn = "idle" | "connecting" | "open" | "closed";

/** A brief the orchestrator produced but is holding for the dispatcher (a consideration below 0.5 confidence). */
export interface HeldBrief { version: number; needsReview: string[]; brief: Brief }

export interface LiveState {
  mode: Mode;
  conn: Conn;
  incidentId: string | null;
  incident: IncidentRecord | null;
  segments: CallSegment[];
  /** What the extractor understood from each caller line, by segKey(). Only lines seen live; not kept across a reload. */
  extracted: Record<string, ExtractedFact[]>;
  candidates: BuildingCandidate[];
  brief: Brief | null;
  held: HeldBrief | null;
  progress: Progress | null;
  suggestions: CopilotSuggestion[];
  scripts: ScriptInfo[];
  busy: string | null;     // what the dispatcher just asked for, until the next event
  error: string | null;
  /** Last overlay.updated overlay and last event type: handed to the house viewer (house/liveFeed.ts), no second socket. */
  overlay: Record<string, unknown> | null;
  lastEvent: string | null;
}

const STORE_KEY = "flamescout.incident";
const initialState = (mode: Mode): LiveState => ({
  mode, conn: "idle", incidentId: null, incident: null, segments: [], extracted: {}, candidates: [], brief: null, held: null,
  progress: null, suggestions: [], scripts: [], busy: null, error: null, overlay: null, lastEvent: null,
});

const readStored = (): string | null => {
  const q = new URLSearchParams(window.location.search).get("incident");
  if (q && q !== "off") return q;   // ?incident=off only turns the viewer overlay off
  try { return window.localStorage.getItem(STORE_KEY); } catch { return null; }
};
const store = (id: string | null) => {
  try { id ? window.localStorage.setItem(STORE_KEY, id) : window.localStorage.removeItem(STORE_KEY); } catch { /* private mode */ }
  const url = new URL(window.location.href);
  if (INCIDENT_OFF) return;   // keep ?incident=off in the URL
  id ? url.searchParams.set("incident", id) : url.searchParams.delete("incident");
  window.history.replaceState(window.history.state, "", url);
};

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));
export const segKey = (s: CallSegment) => `${s.call_id}|${s.t_start}|${s.text}`;

/** `forceMock` (?mock=1) skips the API entirely. */
export function useLiveIncident(forceMock: boolean) {
  const [s, setS] = useState<LiveState>(() => initialState(forceMock ? "mock" : "checking"));
  const ws = useRef<WebSocket | null>(null);
  const retry = useRef<number | undefined>(undefined);
  const wanted = useRef<string | null>(null);   // incident the socket should stay connected to

  const patch = useCallback((p: Partial<LiveState> | ((prev: LiveState) => Partial<LiveState>)) =>
    setS((prev) => ({ ...prev, ...(typeof p === "function" ? p(prev) : p) })), []);

  // ---- event reducer: one WebSocket message -> state ----
  const onEvent = useCallback((ev: ApiEvent) => {
    const p = ev.payload as Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
    if (typeof ev.type === "string") patch({ lastEvent: ev.type });
    switch (ev.type) {
      case "overlay.updated": {
        const overlay = overlayFromEvent(ev);
        return overlay ? patch({ overlay }) : undefined;
      }
      case "incident.updated":
        return patch({ incident: p.incident as IncidentRecord });
      case "transcript.segment": {
        const seg = p.segment as CallSegment;
        const extracted = (p.extracted as ExtractedFact[] | undefined) ?? [];
        return patch((prev) => (prev.segments.some((x) => segKey(x) === segKey(seg)) ? {}
          : { segments: [...prev.segments, seg], extracted: { ...prev.extracted, [segKey(seg)]: extracted } }));
      }
      case "location.candidates":
        return patch({ candidates: p.candidates as BuildingCandidate[] });
      case "copilot.suggestion":
        return patch({ suggestions: (p.suggestions as CopilotSuggestion[]).filter((x) => x.status === "open") });
      case "dossier.progress":
        // Only the orchestrator's plan has todos; the rules pipeline sends a different stage payload.
        return Array.isArray(p.todos) ? patch({ progress: p as unknown as Progress }) : undefined;
      case "brief.updated": {
        const brief = p.brief as Brief;
        const published = p.published !== false && brief.published !== false;
        return published
          ? patch({ brief, held: null, busy: null })
          : patch({ held: { version: brief.version, needsReview: (p.needs_review as string[]) ?? [], brief }, busy: null });
      }
      default:
        return undefined;   // contradiction.raised (incident.updated covers it), metric.tick
    }
  }, [patch]);

  // ---- WebSocket with reconnect ----
  const connect = useCallback((id: string) => {
    wanted.current = id;
    window.clearTimeout(retry.current);
    ws.current?.close();
    patch({ conn: "connecting" });
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${window.location.host}/ws/incidents/${id}`);
    ws.current = sock;
    sock.onopen = () => patch({ conn: "open", error: null });
    sock.onmessage = (m) => { try { onEvent(JSON.parse(m.data as string) as ApiEvent); } catch { /* ignore a malformed frame */ } };
    sock.onclose = (e) => {
      if (ws.current !== sock) return;
      if (e.code === 4404) {              // the API no longer knows this incident (restart without Tiger, or deleted)
        wanted.current = null; store(null);
        return patch({ ...initialState("live"), scripts: [], error: "That incident no longer exists. Start a new call." });
      }
      patch({ conn: "closed" });
      if (wanted.current === id) retry.current = window.setTimeout(() => connect(id), 2000);
    };
  }, [onEvent, patch]);

  // Everything the socket does not replay: after (re)connect, rebuild what we can from REST.
  const hydrate = useCallback(async (id: string) => {
    const [cands, brief, sugg, ov] = await Promise.allSettled([api.candidates(id), api.latestBrief(id), api.copilot(id), api.latestOverlay(id)]);
    patch((prev) => ({
      candidates: cands.status === "fulfilled" && cands.value.length ? cands.value : prev.candidates,
      brief: brief.status === "fulfilled" ? brief.value : prev.brief,
      suggestions: sugg.status === "fulfilled" ? sugg.value : prev.suggestions,
      overlay: prev.overlay ?? (ov.status === "fulfilled" ? overlayFromEvent({ type: "overlay.updated", payload: ov.value }) : null),
    }));
  }, [patch]);

  // ---- startup: is the API there? ----
  useEffect(() => {
    if (forceMock) return;
    let alive = true;
    (async () => {
      try {
        const scripts: ScriptInfo[] = await api.scripts(2500);   // any failure here means the API is down: mock mode
        if (!alive) return;
        patch({ mode: "live", scripts });
        const id = readStored();
        if (!id) return;
        try {
          const incident = await api.getIncident(id);
          if (!alive) return;
          patch({ incidentId: id, incident });
          connect(id);
          void hydrate(id);
        } catch { store(null); }   // stale id: start clean
      } catch {
        if (alive) patch({ mode: "mock" });
      }
    })();
    return () => { alive = false; wanted.current = null; window.clearTimeout(retry.current); ws.current?.close(); };
  }, [forceMock, connect, hydrate, patch]);

  // Share the incident with the house viewer (BuildingViewer reads it instead of opening its own socket).
  useEffect(() => {
    publishLiveFeed({ mode: s.mode, incidentId: s.incidentId, conn: s.conn, incident: s.incident, overlay: s.overlay, lastEvent: s.lastEvent });
  }, [s.mode, s.incidentId, s.conn, s.incident, s.overlay, s.lastEvent]);

  const fail = useCallback((e: unknown) => patch({ busy: null, error: message(e) }), [patch]);

  // ---- actions ----
  /** New incident with the socket open before anything is sent (so no event is missed). */
  const openIncident = useCallback(async (busy: string | null): Promise<string> => {
    const rec = await api.createIncident();
    const id = rec.incident_id;
    store(id);
    setS((prev) => ({ ...initialState("live"), scripts: prev.scripts, incidentId: id, incident: rec, busy }));
    connect(id);
    await new Promise<void>((resolve) => {   // wait for the socket to open (max 3 s)
      const t0 = Date.now();
      const tick = () => (ws.current?.readyState === WebSocket.OPEN || Date.now() - t0 > 3000 ? resolve() : window.setTimeout(tick, 50));
      tick();
    });
    return id;
  }, [connect]);

  /** New incident, then replay a scripted call into it. An empty scriptId starts a blank incident to type into. */
  const startCall = useCallback(async (scriptId: string) => {
    patch({ busy: scriptId ? `Starting ${scriptId}…` : "Starting…", error: null });
    try {
      const id = await openIncident(scriptId ? `Starting ${scriptId}…` : null);
      if (scriptId) await api.simulate(id, scriptId);
      patch({ busy: null });
    } catch (e) { fail(e); }
  }, [openIncident, patch, fail]);

  /** One typed transcript line. Creates a blank incident first if there is none. Timing continues after the last line. */
  const sendLine = useCallback(async (text: string, speaker: CallSegment["speaker"], callId: string) => {
    const line = text.trim();
    if (!line) return;
    patch({ error: null });
    try {
      const id = wanted.current ?? (await openIncident(null));
      const last = s.segments.reduce((t, x) => Math.max(t, x.t_end), 0);
      const t_start = s.segments.length ? Math.round((last + 1) * 10) / 10 : 0;
      const t_end = Math.round((t_start + Math.max(1, line.split(/\s+/).length / 2.5)) * 10) / 10;
      await api.addSegment(id, { call_id: callId, speaker, text: line, t_start, t_end });
    } catch (e) { fail(e); }
  }, [s.segments, openIncident, patch, fail]);

  const confirm = useCallback(async (buildingId: string) => {
    const id = wanted.current ?? s.incidentId;
    if (!id) return;
    patch({ busy: "Building the brief…", error: null });
    try { await api.confirmLocation(id, buildingId); } catch (e) { fail(e); }   // brief.updated clears `busy`
  }, [s.incidentId, patch, fail]);

  const resolve = useCallback(async (key: string, value: unknown) => {
    const id = s.incidentId;
    if (!id) return;
    try { patch({ incident: await api.resolveContradiction(id, key, value) }); } catch (e) { fail(e); }
  }, [s.incidentId, patch, fail]);

  /** Publish a held brief as is. */
  const approveHeld = useCallback(async () => {
    const id = s.incidentId;
    if (!id) return;
    patch({ busy: "Publishing…", error: null });
    try { patch({ brief: await api.review(id), held: null, busy: null }); } catch (e) { fail(e); }
  }, [s.incidentId, patch, fail]);

  /** Follow an incident created elsewhere (a Call in from any device): load it, open its socket, rebuild from REST. */
  const attach = useCallback(async (id: string) => {
    if (id === wanted.current) return;
    try {
      const incident = await api.getIncident(id);
      store(id);
      setS((prev) => ({ ...initialState("live"), scripts: prev.scripts, incidentId: id, incident }));
      connect(id);
      void hydrate(id);
    } catch (e) { fail(e); }
  }, [connect, hydrate, fail]);

  const reset = useCallback(() => {
    wanted.current = null; window.clearTimeout(retry.current); ws.current?.close(); store(null);
    setS((prev) => ({ ...initialState("live"), scripts: prev.scripts }));
  }, []);

  const retryApi = useCallback(() => window.location.reload(), []);

  return { state: s, startCall, sendLine, confirm, resolve, approveHeld, attach, reset, retryApi };
}
