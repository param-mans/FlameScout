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
/** Matterport model id first, then catalog id (listing id such as 209217448, or a sample id such as sample-a). */
export function findHouse(catalog: CatalogEntry[], id: string): CatalogEntry | null {
  return catalog.find((h) => h.matterportModelId === id) ?? catalog.find((h) => h.id === id) ?? null;
}
export const hasPlan = (h: CatalogEntry | null) => !!h && !h.no_scan && !!h.matterportModelId;

export type ConfirmedHouse =
  | { state: "none" }                                   // no live incident, or no confirmed building yet
  | { state: "loading"; buildingId: string }
  | { state: "house"; buildingId: string; sampleId: string; address: string }
  | { state: "noScan"; buildingId: string; address: string | null };

/** The live incident's confirmed building, matched against the viewer catalog. */
export function useConfirmedHouse(): ConfirmedHouse {
  const f = useLiveFeed();
  const bid = !TOWER && f.mode === "live" ? f.incident?.confirmed_building_id ?? null : null;
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(() => catalogCache);
  useEffect(() => {
    if (!bid || catalog) return;
    let alive = true;
    void loadCatalog().then((c) => alive && setCatalog(c));
    return () => { alive = false; };
  }, [bid, catalog]);
  if (!bid) return { state: "none" };
  if (!catalog) return { state: "loading", buildingId: bid };
  const h = findHouse(catalog, bid);
  const address = h ? [h.address, h.city].filter(Boolean).join(", ") : null;
  return hasPlan(h) ? { state: "house", buildingId: bid, sampleId: h!.id, address: address ?? h!.id } : { state: "noScan", buildingId: bid, address };
}
