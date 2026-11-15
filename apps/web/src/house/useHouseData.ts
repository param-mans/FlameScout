// Data for the MDT house view: catalog entry, house document (plan, quality, scan dates, listing record) from the
// house-data server, and the incident state reported by the viewer. The viewer's messages are already filtered by origin
// and source in BuildingViewer; here the overlay (and the planner's routes) are validated against the plan index with the
// same validator the viewer uses (public/house-viewer/incident/validate.js) before the UI shows anything from them.
import { useCallback, useEffect, useRef, useState } from "react";
import type { ViewerIncidentState } from "../components/viewer/BuildingViewer";
import type { CatalogHouse, HouseDoc, Overlay } from "./houseBrief";

const BASE = `${import.meta.env.BASE_URL}house-viewer/`;
type Validate = (overlay: unknown, planIndex: unknown) => string[];
let validatorP: Promise<Validate> | null = null;
const loadValidator = () =>
  (validatorP ??= import(/* @vite-ignore */ `${BASE}incident/validate.js`).then((m: { validate: Validate }) => m.validate));

/** A house catalog entry (data/raw/catalog.csv, not in samples/index.json) from the house data service's document, which
 *  carries the same fields (id, address, matterportModelId, listing; city and market inside location). */
function fromDoc(d: HouseDoc, sampleId: string): CatalogHouse | null {
  const x = d as unknown as Partial<CatalogHouse> & { location?: { lat?: number; lon?: number; city?: string; market?: string } | null };
  if (!x || x.id !== sampleId || typeof x.address !== "string") return null;
  const loc = x.location, hasLatLon = typeof loc?.lat === "number" && typeof loc?.lon === "number";
  return {
    id: sampleId, address: x.address, city: loc?.city ?? undefined, market: loc?.market ?? undefined,
    matterportModelId: x.matterportModelId ?? null, no_scan: x.no_scan, listing: x.listing ?? null,
    location: hasLatLon ? { lat: loc!.lat!, lon: loc!.lon! } : null,
  };
}

export function useHouseData(sampleId: string | null) {
  const [sample, setSample] = useState<CatalogHouse | null>(null);
  const [doc, setDoc] = useState<HouseDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [incident, setIncident] = useState<{ overlay: Overlay | null; routes: number; noRoute: string[] }>({ overlay: null, routes: 0, noRoute: [] });
  const planIndex = useRef<unknown>(null);

  useEffect(() => {
    let alive = true;
    setDoc(null); setError(null); setIncident({ overlay: null, routes: 0, noRoute: [] });
    if (!sampleId) return;
    Promise.all([
      fetch(`${BASE}samples/index.json`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`catalog HTTP ${r.status}`)))),
      fetch(`/api/house-viewer/sample/${encodeURIComponent(sampleId)}`).then(async (r) => { const j = await r.json().catch(() => null); if (!r.ok) throw new Error(j?.error ?? `HTTP ${r.status}`); return j; }),
    ]).then(([list, d]: [CatalogHouse[], HouseDoc]) => {
      if (!alive) return;
      setSample(list.find((x) => x.id === sampleId) ?? fromDoc(d, sampleId));
      setDoc(d);
    }).catch((e: Error) => { if (alive) { setError(e.message); fetch(`${BASE}samples/index.json`).then((r) => r.json()).then((list: CatalogHouse[]) => alive && setSample(list.find((x) => x.id === sampleId) ?? null)).catch(() => {}); } });
    return () => { alive = false; };
  }, [sampleId]);

  const onPlanIndex = useCallback((index: unknown) => { planIndex.current = index; }, []);
  const onIncidentState = useCallback(async (s: ViewerIncidentState) => {
    if (!s.overlay) return setIncident({ overlay: null, routes: 0, noRoute: [] });
    if (!planIndex.current) return; // the viewer always sends its plan index first
    const validate = await loadValidator();
    const errors = validate({ ...s.overlay, routes: s.routes }, planIndex.current);
    if (errors.length) { console.warn("[house-view] incident state from the viewer failed validation; ignored", errors); return; }
    setIncident({ overlay: s.overlay as unknown as Overlay, routes: s.routes.length, noRoute: s.noRoute });
  }, []);

  return { sample, doc, error, incident, onPlanIndex, onIncidentState };
}
