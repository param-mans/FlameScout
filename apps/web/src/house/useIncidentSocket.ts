// Live incident overlay for the MDT's main viewer. Three sources, in this order:
//   ?incident=off or ?mock=tower   off: no WebSocket, no state changes
//   the dashboard's live incident  (src/api/useLiveIncident.ts, shared through house/liveFeed.ts) when the API is up:
//                                  its socket's overlay.updated events are used, no second socket; follows the
//                                  incident the app has (the one created on load or by Run call)
//   ?incident=<id> with no API     (e.g. the WebSocket mock) the viewer opens /ws/incidents/<id> itself
//   ?ws=<url>                      optional, for local tests: another WebSocket URL, accepted only on this page's hostname
//
// Only overlay.updated is used; other events and malformed messages are ignored. Own socket reconnects after 1, 2, 4,
// 5, 5... s. The overlay is not validated here: the viewer validates it against the plan (incident/validate.js) and
// answers incidentApplied / incidentRejected, which BuildingViewer reports back.
//
// House rule: overlay.model_id is matched against the viewer catalog (public/house-viewer/samples/index.json), first
// on the Matterport model id (matterportModelId), then on the catalog id (listing id such as 209217448, or a sample id
// such as sample-a). No match: the overlay is not applied and the chip says "Unknown building: <model_id>".
import { useEffect, useMemo, useState } from "react";
import { findHouse, INITIAL_INCIDENT, INCIDENT_OFF, loadCatalog, TOWER, useLiveFeed } from "./liveFeed";

type Json = Record<string, unknown>;
export type LiveIncident = {
  enabled: boolean;
  incidentId: string | null;
  connection: "connecting" | "open" | "closed";
  overlay: Json | null; // last overlay.updated whose house is in the catalog
  version: number | null;
  sampleId: string | null; // catalog id of that overlay's house
  unknownModel: string | null; // model_id of the last overlay that matched no house
  lastEvent: string | null; // type of the last event seen on the socket
  lastModelId: string | null; // model_id of the last overlay received (matched or not)
};

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

/** ?incident=<id> (and ?ws=<url>) from the page URL, or null when the live overlay is off. */
export function readIncidentParams(): { id: string; url: string } | null {
  const q = new URLSearchParams(window.location.search);
  const id = INITIAL_INCIDENT;
  if (TOWER || !id || !ID_RE.test(id)) return null;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  let url = `${proto}//${window.location.host}/ws/incidents/${encodeURIComponent(id)}`;
  const override = q.get("ws");
  if (override) {
    try {
      const u = new URL(override);
      if ((u.protocol === "ws:" || u.protocol === "wss:") && u.hostname === window.location.hostname) url = u.href;
      else console.warn("[incident] ?ws= ignored: only ws(s) URLs on this page's hostname are accepted");
    } catch { console.warn("[incident] ?ws= ignored: not a URL"); }
  }
  return { id, url };
}

/** The one place that knows the event shape. Real shape (services/api/service.py, _emit_overlay):
 *    { type: "overlay.updated", incident_id, ts, trace_id, payload: { version, overlay } }
 *  The flat { type, version, overlay } form described in docs/agents-api.md is accepted too. */
export function overlayFromEvent(msg: unknown): Json | null {
  if (!msg || typeof msg !== "object") return null;
  const m = msg as Json;
  if (m.type !== "overlay.updated") return null;
  const payload = m.payload && typeof m.payload === "object" ? (m.payload as Json) : m;
  const ov = payload.overlay;
  if (!ov || typeof ov !== "object" || Array.isArray(ov)) return null;
  const o = ov as Json;
  return typeof o.model_id === "string" && typeof o.version === "number" ? o : null;
}

export type IncidentResult = { kind: "applied"; version: number } | { kind: "rejected"; version: number | null; errors: string[] };

/** Chip text for the live overlay. */
export function liveText(live: LiveIncident, result: IncidentResult | null, lastApplied: number | null): string {
  if (live.unknownModel) return `Unknown building: ${live.unknownModel}`;
  if (result?.kind === "rejected") return `REJECTED v${result.version ?? "?"} (${lastApplied != null ? `keeping v${lastApplied}` : "nothing applied"})`;
  if (result?.kind === "applied") return `LIVE · v${result.version}`;
  const id = live.incidentId ? ` · ${shortId(live.incidentId)}` : "";
  return `LIVE${id} · ${live.connection === "open" ? "waiting for overlay" : live.connection === "connecting" ? "connecting…" : "offline, retrying"}`;
}

/** "inc_e305c9a1b2" -> "inc_e305…" */
export const shortId = (id: string) => (id.length > 9 ? `${id.slice(0, 8)}…` : id);

type Overlay = { overlay: Json | null; version: number | null; sampleId: string | null; unknownModel: string | null; lastModelId: string | null };
const NO_OVERLAY: Overlay = { overlay: null, version: null, sampleId: null, unknownModel: null, lastModelId: null };

/** overlay -> house of the catalog (async: the catalog is fetched once). */
async function resolveOverlay(ov: Json): Promise<Overlay> {
  const house = findHouse(await loadCatalog(), ov.model_id as string);
  const lastModelId = ov.model_id as string;
  return house
    ? { overlay: ov, version: ov.version as number, sampleId: house.id, unknownModel: null, lastModelId }
    : { ...NO_OVERLAY, unknownModel: lastModelId, lastModelId };
}

/** enabled: the caller is the MDT's main viewer (one per page). */
export function useIncidentSocket(enabled: boolean): LiveIncident {
  const feed = useLiveFeed();
  const on = enabled && !TOWER && !INCIDENT_OFF;
  // the app's incident, when the API is up and it has one
  const appId = on && feed.mode === "live" ? feed.incidentId : null;
  // own socket: only with an explicit ?incident=<id> and the API unreachable (mock mode)
  const params = useMemo(() => (on && feed.mode === "mock" ? readIncidentParams() : null), [on, feed.mode]);
  const waitingForApi = on && feed.mode === "checking" && !!readIncidentParams();

  // ---- app source: no socket, the dashboard's events ----
  const [app, setApp] = useState<{ id: string | null; ov: Overlay }>({ id: null, ov: NO_OVERLAY });
  const appOverlay = appId ? feed.overlay : null;
  useEffect(() => {
    if (!appId) return;
    if (!appOverlay) return setApp({ id: appId, ov: NO_OVERLAY });
    let alive = true;
    void resolveOverlay(appOverlay).then((r) => {
      if (!alive) return;
      setApp((a) => {
        const prev = a.id === appId ? a.ov : NO_OVERLAY;
        return { id: appId, ov: r.overlay ? r : { ...prev, unknownModel: r.unknownModel, lastModelId: r.lastModelId } };
      });
    });
    return () => { alive = false; };
  }, [appId, appOverlay]);

  // ---- own socket (explicit ?incident=, no API) ----
  const own = useOwnSocket(params);

  if (appId) {
    const ov = app.id === appId ? app.ov : NO_OVERLAY;
    const connection = feed.conn === "open" ? "open" : feed.conn === "closed" ? "closed" : "connecting";
    return { enabled: true, incidentId: appId, connection, lastEvent: feed.lastEvent, ...ov };
  }
  if (params) return own;
  if (waitingForApi) return { enabled: true, incidentId: readIncidentParams()!.id, connection: "connecting", lastEvent: null, ...NO_OVERLAY };
  return { enabled: false, incidentId: null, connection: "closed", lastEvent: null, ...NO_OVERLAY };
}

function useOwnSocket(params: { id: string; url: string } | null): LiveIncident {
  const [state, setState] = useState<LiveIncident>({
    enabled: !!params, incidentId: params?.id ?? null, connection: "connecting", lastEvent: null, ...NO_OVERLAY,
  });

  useEffect(() => {
    if (!params) return;
    let alive = true, ws: WebSocket | null = null, timer: number | undefined, attempt = 0;
    setState({ enabled: true, incidentId: params.id, connection: "connecting", lastEvent: null, ...NO_OVERLAY });

    const onOverlay = async (ov: Json) => {
      const r = await resolveOverlay(ov);
      if (!alive) return;
      setState((s) => (r.overlay ? { ...s, ...r } : { ...s, unknownModel: r.unknownModel, lastModelId: r.lastModelId }));
    };
    const connect = () => {
      if (!alive) return;
      setState((s) => ({ ...s, connection: "connecting" }));
      ws = new WebSocket(params.url);
      ws.onopen = () => { attempt = 0; setState((s) => ({ ...s, connection: "open" })); };
      ws.onmessage = (e) => {
        let msg: unknown;
        try { msg = JSON.parse(typeof e.data === "string" ? e.data : ""); } catch { return; } // malformed: ignored
        const type = msg && typeof msg === "object" ? (msg as Json).type : null;
        if (typeof type === "string") setState((s) => (s.lastEvent === type ? s : { ...s, lastEvent: type }));
        const ov = overlayFromEvent(msg);
        if (ov) void onOverlay(ov);
      };
      ws.onclose = () => {
        if (!alive) return;
        setState((s) => ({ ...s, connection: "closed" }));
        timer = window.setTimeout(connect, Math.min(5000, 1000 * 2 ** attempt++));
      };
      ws.onerror = () => {}; // onclose follows and schedules the retry
    };
    connect();
    return () => {
      alive = false; window.clearTimeout(timer);
      if (!ws) return;
      ws.onclose = null; ws.onmessage = null;
      // closing a socket that is still connecting makes the browser log an error: close it once it opens instead
      if (ws.readyState === WebSocket.CONNECTING) { const w = ws; w.onopen = () => w.close(); } else ws.close();
    };
  }, [params]);

  return state;
}
