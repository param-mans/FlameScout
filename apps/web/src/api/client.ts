import type { Brief, BuildingCandidate, CallSegment, CopilotSuggestion, IncidentRecord } from "../types/schemas";

// REST client for the FastAPI backend. Vite proxies /api and /ws to :8000 (vite.config.ts).
// Roles: the API reads X-Role (default "dispatcher"); only the data-mode admin route needs "admin".

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, timeoutMs = 15000): Promise<T> {
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
    if (!res.ok) {
      const detail = await res.json().then((j: { detail?: unknown }) => j.detail, () => undefined);
      throw new ApiError(res.status, typeof detail === "string" ? detail : `HTTP ${res.status}`);
    }
    return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
  } finally {
    window.clearTimeout(timer);
  }
}

export interface ScriptInfo {
  script_id: string; description?: string | null; segments: number;
  /** From data/fixtures/calls/manifest.json; older APIs omit them. */
  title?: string | null; group?: "showcase" | "test" | string | null; inconsistent?: boolean | null; house?: string | null;
}

export interface TimeToBrief {
  source: "tiger" | "workspace";
  n: number;
  n_fallback?: number;
  p50_ms: number | null;
  p95_ms: number | null;
}

export const api = {
  /** Measured time from a confirmed address to the first brief (Tiger Data view when configured). */
  timeToBrief: () => request<TimeToBrief>("GET", "/api/metrics/time-to-brief", undefined, 8000),
  // Not /health: Vite (and the deploy proxy) only forward /api and /ws, so /health would return the page's HTML.
  scripts: (timeoutMs = 15000) => request<ScriptInfo[]>("GET", "/api/calls/scripts", undefined, timeoutMs),
  createIncident: () => request<IncidentRecord>("POST", "/api/incidents", {}),
  getIncident: (id: string) => request<IncidentRecord>("GET", `/api/incidents/${id}`),
  /** Replays a scripted call into an existing incident with the original timing; returns at once (202). */
  simulate: (incidentId: string, scriptId: string, speed = 1) =>
    request<{ incident_id: string; status: string }>("POST", "/api/calls/simulate", {
      script_id: scriptId, incident_id: incidentId, realtime: true, speed,
    }),
  /** One transcript line; the result also reaches the socket as transcript.segment / incident.updated. */
  addSegment: (id: string, seg: CallSegment) => request<unknown>("POST", `/api/incidents/${id}/segments`, seg),
  candidates: (id: string) => request<BuildingCandidate[]>("GET", `/api/incidents/${id}/candidates`),
  /** Returns at once (202); the brief arrives as `brief.updated` on the WebSocket. */
  confirmLocation: (id: string, buildingId: string | null) =>
    request<unknown>("POST", `/api/incidents/${id}/confirm-location`, { building_id: buildingId }),
  review: (id: string, drop: string[] = []) => request<Brief>("POST", `/api/incidents/${id}/review`, { drop }),
  resolveContradiction: (id: string, key: string, value: unknown) =>
    request<IncidentRecord>("POST", `/api/incidents/${id}/contradictions/resolve`, { key, value }),
  latestBrief: (id: string) => request<Brief>("GET", `/api/incidents/${id}/briefs/latest?published_only=true`),
  latestOverlay: (id: string) => request<{ version: number; overlay: Record<string, unknown> }>("GET", `/api/incidents/${id}/overlay/latest`),
  copilot: (id: string) => request<CopilotSuggestion[]>("GET", `/api/incidents/${id}/copilot`),
  /** Crew feedback on a fact key or marker id of the latest brief; remembered for the building (long-term memory). */
  feedback: (id: string, itemId: string, verdict: "confirmed" | "wrong" | "outdated", note?: string) =>
    request<unknown>("POST", `/api/incidents/${id}/feedback`, { fact_key: itemId, verdict, note: note ?? null }),
  /** Call in: starts a call every open web app follows (/ws/calls). The recording goes to /api/calls/web/callin/audio. */
  callIn: () => request<{ incident_id: string; call_label: string }>("POST", "/api/calls/web/callin", {}),
  endCall: (c: { incident_id: string; call_label: string }) => request<unknown>("POST", "/api/calls/web/end", c),
  clearCalls: () => request<unknown>("POST", "/api/calls/web/clear", {}),
  /** Ask the caller for their camera: a link, sent by iMessage (Photon Stable) when configured, else share it by hand. */
  cameraInvite: (id: string, phone: string | null) =>
    request<{ camera: CameraInfo; url: string; message: CameraMessage }>(
      "POST", `/api/incidents/${id}/camera/invite`, { phone: phone || null }),
  /** The caller's phone opens the texted link. */
  cameraByToken: (token: string) => request<{ camera: CameraInfo }>("GET", `/api/camera/${encodeURIComponent(token)}`),
};

/** A camera call in an incident (camera.status events, /api/.../camera). */
export interface CameraInfo {
  incident_id: string;
  call_id: string;
  status: "invited" | "streaming" | "ended";
  frames: number;
  observations: number;
  sent_to?: string | null;
  invite?: CameraMessage | null;
}

/** The camera link sent by iMessage (Photon Stable, via the messenger service). */
export interface CameraMessage {
  state: "sent" | "failed" | "not_configured" | "none";
  detail: string;
  line?: string | null;
}
