// Live incident overlay from the backend, ONLY when the page has ?incident=<id>. Without it nothing here runs: no
// WebSocket, no fetch, no state changes (the MDT behaves exactly as before).
//
//   ?incident=<id>   opens /ws/incidents/<id> on this host (ws: or wss: like the page; Vite proxies /ws to :8000)
//   ?ws=<url>        optional, for local tests: another WebSocket URL, accepted only on this page's hostname
//
// Only overlay.updated is used; other events and malformed messages are ignored. Reconnects after 1, 2, 4, 5, 5... s.
// The overlay is not validated here: the viewer validates it against the plan (incident/validate.js) and answers
// incidentApplied / incidentRejected, which BuildingViewer reports back.
//
// House rule: overlay.model_id is matched against the viewer catalog (public/house-viewer/samples/index.json), first
// on the Matterport model id (matterportModelId), then on the catalog id (listing id such as 209217448, or a sample id
// such as sample-a). No match: the overlay is not applied and the chip says "Unknown building: <model_id>".
import { useEffect, useRef, useState } from "react";

type Json = Record<string, unknown>;
export type LiveIncident = {
  enabled: boolean;
  incidentId: string | null;
  connection: "connecting" | "open" | "closed";
  overlay: Json | null; // last overlay.updated whose house is in the catalog
  version: number | null;
  sampleId: string | null; // catalog id of that overlay's house
  unknownModel: string | null; // model_id of the last overlay that matched no house
};

const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const BASE = `${import.meta.env.BASE_URL}house-viewer/`;

/** ?incident=<id> (and ?ws=<url>) from the page URL, or null when the live overlay is off. */
export function readIncidentParams(): { id: string; url: string } | null {
  const q = new URLSearchParams(window.location.search);
  const id = q.get("incident");
  if (!id || !ID_RE.test(id)) return null;
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
  return live.connection === "open" ? "LIVE · waiting for overlay" : live.connection === "connecting" ? "LIVE · connecting…" : "LIVE · offline, retrying";
}

type CatalogEntry = { id: string; matterportModelId: string | null };
function houseFor(catalog: CatalogEntry[], modelId: string): string | null {
  return (catalog.find((h) => h.matterportModelId === modelId) ?? catalog.find((h) => h.id === modelId))?.id ?? null;
}

/** enabled: the caller is the MDT's main viewer (one socket per page); the URL must also have ?incident=. */
export function useIncidentSocket(enabled: boolean): LiveIncident {
  const [params] = useState(() => (enabled ? readIncidentParams() : null));
  const [state, setState] = useState<LiveIncident>({
    enabled: !!params, incidentId: params?.id ?? null, connection: "connecting", overlay: null, version: null, sampleId: null, unknownModel: null,
  });
  const catalog = useRef<Promise<CatalogEntry[]> | null>(null);

  useEffect(() => {
    if (!params) return;
    let alive = true, ws: WebSocket | null = null, timer: number | undefined, attempt = 0;
    catalog.current ??= fetch(`${BASE}samples/index.json`).then((r) => (r.ok ? r.json() : [])).catch(() => []);

    const onOverlay = async (ov: Json) => {
      const list = await catalog.current!;
      if (!alive) return;
      const id = houseFor(list, ov.model_id as string);
      setState((s) => (id
        ? { ...s, overlay: ov, version: ov.version as number, sampleId: id, unknownModel: null }
        : { ...s, unknownModel: ov.model_id as string }));
    };
    const connect = () => {
      if (!alive) return;
      setState((s) => ({ ...s, connection: "connecting" }));
      ws = new WebSocket(params.url);
      ws.onopen = () => { attempt = 0; setState((s) => ({ ...s, connection: "open" })); };
      ws.onmessage = (e) => {
        let msg: unknown;
        try { msg = JSON.parse(typeof e.data === "string" ? e.data : ""); } catch { return; } // malformed: ignored
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
