// What the dashboard's live client (src/api/useLiveIncident.ts) knows about the current incident, shared with the
// house viewer without a second WebSocket: useLiveIncident publishes, BuildingViewer / the MDT read.
//
// URL switches (read once, at load, before useLiveIncident rewrites ?incident=):
//   ?incident=off   the viewer's live overlay is off (the dashboard still works)
//   ?mock=tower     tower mock: nothing here is used (no feed, no socket, no confirmed house)
import { useEffect, useState, useSyncExternalStore } from "react";
import type { IncidentRecord } from "../types/schemas";

type Json = Record<string, unknown>;
export type FeedMode = "checking" | "live" | "mock";
export type FeedConn = "idle" | "connecting" | "open" | "closed";
export type LiveFeed = {
  mode: FeedMode;
  incidentId: string | null;
  conn: FeedConn;
  incident: IncidentRecord | null;
  /** last overlay.updated of this incident (kept, so a viewer mounted later still gets it) */
  overlay: Json | null;
  /** type of the last WebSocket event of this incident */
  lastEvent: string | null;
};

const q = new URLSearchParams(window.location.search);
export const TOWER = q.get("mock") === "tower";
export const INCIDENT_OFF = q.get("incident") === "off";
/** ?incident=<id> as the page was opened (not the id useLiveIncident writes later). */
export const INITIAL_INCIDENT = !INCIDENT_OFF ? q.get("incident") : null;

const EMPTY: LiveFeed = { mode: "checking", incidentId: null, conn: "idle", incident: null, overlay: null, lastEvent: null };
let feed: LiveFeed = EMPTY;
const subs = new Set<() => void>();

export function publishLiveFeed(next: LiveFeed) {
  if (TOWER) return;
  const same = (Object.keys(next) as (keyof LiveFeed)[]).every((k) => next[k] === feed[k]);
  if (same) return;
  feed = next;
  subs.forEach((f) => f());
}
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
export const useLiveFeed = (): LiveFeed => useSyncExternalStore(subscribe, () => feed);

// ---- viewer catalog (public/house-viewer/samples/index.json) ----
export type CatalogEntry = { id: string; matterportModelId: string | null; no_scan?: boolean; address?: string; city?: string; name?: string };
let catalogP: Promise<CatalogEntry[]> | null = null;
let catalogCache: CatalogEntry[] | null = null;   // once loaded: lets a new component match synchronously
export function loadCatalog(): Promise<CatalogEntry[]> {
  catalogP ??= fetch(`${import.meta.env.BASE_URL}house-viewer/samples/index.json`)
    .then((r) => (r.ok ? r.json() : []))
    .then((l: unknown) => (Array.isArray(l) ? (l as CatalogEntry[]) : []))
    .catch(() => [] as CatalogEntry[])
    .then((l) => (catalogCache = l));
  return catalogP;
}
/** Matterport model id first, then catalog id (listing id such as 209217448, or a sample id such as sample-a).
 *  The loaded catalog also holds the house catalog entries already fetched by lookupHouse (appended to the same list). */
export function findHouse(catalog: CatalogEntry[], id: string): CatalogEntry | null {
  return catalog.find((h) => h.matterportModelId === id) ?? catalog.find((h) => h.id === id) ?? null;
}

// ---- house catalog fallback (data/raw/catalog.csv, served by the house data service) ----
// A listing id that is not in samples/index.json may still be a catalog house: GET /api/house-viewer/sample/<id> resolves
// it (apps/web/server/handler.mjs). The entry is added to the loaded catalog, so findHouse (and the overlay's model id
// lookup in useIncidentSocket) finds it afterwards. Unknown ids (404) are remembered; network errors are retried later.
const LISTING_ID = /^[0-9]{1,20}$/;
const extraP = new Map<string, Promise<CatalogEntry | null>>();
function fetchCatalogHouse(id: string): Promise<CatalogEntry | null> {
  let p = extraP.get(id);
  if (!p) {
    p = fetch(`/api/house-viewer/sample/${encodeURIComponent(id)}`)
      .then(async (r) => {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = (await r.json()) as { id?: unknown; address?: unknown; matterportModelId?: unknown; no_scan?: unknown };
        if (d?.id !== id) return null;
        return { id, address: typeof d.address === "string" ? d.address : undefined, no_scan: d.no_scan === true,
                 matterportModelId: typeof d.matterportModelId === "string" ? d.matterportModelId : null } as CatalogEntry;
      })
      .catch(() => { extraP.delete(id); return null; });
    extraP.set(id, p);
  }
  return p;
}
/** The viewer catalog entry for id, else (listing ids only) the house catalog's through the house data service. */
export async function lookupHouse(id: string): Promise<CatalogEntry | null> {
  const catalog = await loadCatalog();
  const h = findHouse(catalog, id);
  if (h || !LISTING_ID.test(id)) return h;
  const extra = await fetchCatalogHouse(id);
  if (extra && catalogCache && !findHouse(catalogCache, id)) catalogCache.push(extra);
  return extra;
}
export const hasPlan = (h: CatalogEntry | null) => !!h && !h.no_scan && !!h.matterportModelId;

export type ConfirmedHouse =
  | { state: "none" }                                   // no live incident, or no confirmed building yet
  | { state: "loading"; buildingId: string }
  | { state: "house"; buildingId: string; sampleId: string; address: string }
  | { state: "noScan"; buildingId: string; address: string | null };

/** The live incident's confirmed building, matched against the viewer catalog, then the house catalog (lookupHouse). */
export function useConfirmedHouse(): ConfirmedHouse {
  const f = useLiveFeed();
  const bid = !TOWER && f.mode === "live" ? f.incident?.confirmed_building_id ?? null : null;
  // the building's entry: synchronous when it is already known, else looked up (samples/index.json, then the house catalog)
  const known = bid && catalogCache ? findHouse(catalogCache, bid) : null;
  const [looked, setLooked] = useState<{ id: string; h: CatalogEntry | null } | null>(null);
  useEffect(() => {
    if (!bid || known || looked?.id === bid) return;
    let alive = true;
    void lookupHouse(bid).then((h) => alive && setLooked({ id: bid, h }));
    return () => { alive = false; };
  }, [bid, known, looked]);
  if (!bid) return { state: "none" };
  if (!known && looked?.id !== bid) return { state: "loading", buildingId: bid };
  const h = known ?? looked!.h;
  const address = h ? [h.address, h.city].filter(Boolean).join(", ") : null;
  return hasPlan(h) ? { state: "house", buildingId: bid, sampleId: h!.id, address: address ?? h!.id } : { state: "noScan", buildingId: bid, address };
}
