// houseBrief: builds the MDT Brief for a house from what we actually have: the listing (catalog + listing record), the
// floor plan (Matterport API, via the house-data server) and the incident overlay received by the viewer.
// It uses the existing Brief type, so it can be swapped for the pipeline's real Brief later; HouseExtras carries what the
// Brief does not model yet (plan counts, listing-vs-plan pairs, unknowns). Nothing is invented: missing data stays null
// and the UI shows "Not available", never a zero or a filler value.
import type { Brief, Consideration, Evidence, Fact, Marker } from "../types/schemas";

// ---------------------------------------------------------------- inputs (house-data server and viewer messages)
export type CatalogHouse = {
  id: string; address: string; city?: string; market?: string; matterportModelId: string | null; no_scan?: boolean;
  location?: { lat: number; lon: number } | null; name?: string;
  listing?: { beds?: number | null; baths?: number | null; sqft?: number | null; yearBuilt?: number | null; homeType?: string | null } | null;
};
type Opening = { id: string; type: string; exterior?: boolean };
type Room = { id: string; label?: string; type?: string | null; areaM2?: number | null };
export type PlanFloor = { id: string; label: string; rooms: Room[]; openings: Opening[] };
export type HouseDoc = {
  id: string; no_scan?: boolean; plan: { floors: PlanFloor[]; stairs?: { id: string }[] } | null;
  quality: { estado?: string; ratio_superficie?: number | string; superficie_plano_sqft?: number } | null;
  scan: { created: string | null; modified: string | null; source: string } | null;
  listing_record: {
    stories: number | null; state: string | null; street: string | null; city: string | null; mls_property_type: string | null;
    baths_full: number | null; baths_half: number | null; remarks: string | null;
    features: { name: string; category: string | null; subcategory1: string | null; subcategory2: string | null }[];
  } | null;
};
type Loc = { kind: "room" | "anchor" | "floor" | "unknown"; room_id?: string; floor_id?: string; floor_reported?: number; anchor?: { room_id: string; position: string; ref_id?: string } };
type Entity = { id: string; status: string; confidence: number; evidence_ids: string[]; location?: Loc; kind?: string; count?: number; count_range?: [number, number]; group_id?: string };
export type Overlay = {
  incident_id: string; version: number; as_of: string; changes_since_last?: string[];
  occupants?: Entity[]; fire?: Entity[]; hazards?: Entity[]; blocked?: Entity[];
  considerations?: { id: string; text: string; rationale: string; confidence: number; evidence_ids: string[]; refs: string[] }[];
};

// ---------------------------------------------------------------- output
export type Row = { label: string; value: string; detail?: string; status?: Fact["status"] };
export type HouseConsideration = { id: string; text: string; rationale: string; level: "High" | "Medium" | "Low" | null; evidence: string[]; refs: string[]; source: "brief" | "incident" };
export type HouseExtras = {
  header: { type: string; address: string; incident: string | null };
  scan: { label: string; created: string | null; months: number | null };
  cards: { incident: string; sleeping: string; exits: string; scan: string; scanPartial: boolean };
  sizeUp: Row[];
  hazards: Row[];
  unknowns: string[];
  considerations: HouseConsideration[];
};

export const NA = "Not available";
const FT2 = 10.7639;
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : null);
const sqft = (v: number) => `${Math.round(v).toLocaleString("en-US")} sq ft (${Math.round(v / FT2).toLocaleString("en-US")} m²)`;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const monthsSince = (iso: string, now: Date) => Math.max(0, (now.getUTCFullYear() - new Date(iso).getUTCFullYear()) * 12 + now.getUTCMonth() - new Date(iso).getUTCMonth());

/** Listing text mentions (remarks + feature names). Fire sprinklers count only as "Fire Sprinkler System". */
const HAZARD_TERMS: [string, RegExp][] = [
  ["Gas", /\b(natural gas|gas (?:cooking|range|stove|water heater|heat|heating|fireplace|log|logs|line|dryer|connection)|propane)\b/i],
  ["Fireplace", /\bfireplaces?\b/i],
  ["Solar panels", /\bsolar\b/i],
  ["Generator", /\bgenerator\b/i],
  ["Garage", /\bgarage\b/i],
  ["Pool", /\bpool\b/i],
  ["Fire sprinklers", /\bfire sprinkler system\b/i],
];
function listingMentions(rec: NonNullable<HouseDoc["listing_record"]>) {
  const texts = [rec.remarks ?? "", ...rec.features.map((f) => [f.name, f.subcategory2].filter(Boolean).join(" "))];
  return HAZARD_TERMS.map(([name, re]) => {
    const hit = texts.find((t) => re.test(t));
    if (!hit) return { name, found: false, snippet: "" };
    const m = re.exec(hit)!, a = Math.max(0, m.index - 30);
    return { name, found: true, snippet: (a > 0 ? "…" : "") + hit.slice(a, m.index + m[0].length + 30).trim() + "…" };
  });
}

export function houseBrief(sample: CatalogHouse, doc: HouseDoc | null, overlay: Overlay | null, now = new Date()): { brief: Brief; extras: HouseExtras } {
  const plan = doc?.plan ?? null, rec = doc?.listing_record ?? null, L = sample.listing ?? null;
  const floors = plan?.floors ?? [];
  const roomById = new Map(floors.flatMap((f, fi) => f.rooms.map((r) => [r.id, { r, fi }] as const)));
  const roomName = (id?: string) => { const x = id ? roomById.get(id) : undefined; return x ? lower(x.r.label || x.r.type || "room") : "an unknown room"; };
  const rooms = floors.flatMap((f, fi) => f.rooms.filter((r) => r.type !== "Void").map((r) => ({ r, fi })));
  const beds = rooms.filter((x) => x.r.type === "Bedroom"), baths = rooms.filter((x) => x.r.type === "Bathroom");
  const exitDoors = (floors[0]?.openings ?? []).filter((o) => o.type === "doorway" && o.exterior === true);
  const stairs = plan?.stairs ?? [];

  // header
  const type = L?.homeType ? (L.homeType === "Detached" ? "House" : L.homeType) : sample.name ?? NA;
  const street = rec?.street ?? sample.address.split(",")[0], city = rec?.city ?? sample.city ?? null;
  const address = [street, city, rec?.state].filter(Boolean).join(", ");
  // scan date: Matterport model creation (the API has no capture date)
  const created = doc?.scan?.created ?? null, months = created ? monthsSince(created, now) : null;
  const scanLabel = created ? `Scan: ${new Date(created).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })} · ${months! < 1 ? "<1" : months} mo` : "Scan date: not available";

  // incident card
  const floorTxt = (loc?: Loc) => {
    if (!loc) return "location unknown";
    if (loc.kind === "room" || loc.kind === "anchor") return roomName(loc.kind === "room" ? loc.room_id : loc.anchor?.room_id) + (loc.kind === "anchor" ? ` (${loc.anchor?.position.replace("_", " ")})` : "");
    if (loc.kind === "floor") {
      const fi = loc.floor_id ? floors.findIndex((f) => f.id === loc.floor_id) : loc.floor_reported != null ? loc.floor_reported - 1 : -1;
      if (fi >= 0 && fi < floors.length) return `floor ${fi + 1}, room unknown`;
      return `Reported floor ${loc.floor_reported ?? "?"}: outside the scanned floors`;
    }
    return "location unknown";
  };
  const occTxt = (o: Entity) => (o.count_range ? `${o.count_range[0]}-${o.count_range[1]}` : String(o.count ?? "?"));
  let incident = "No incident overlay";
  if (overlay) {
    const parts: string[] = [];
    for (const f of overlay.fire ?? []) parts.push(`${(f.kind ?? "fire").replace(/^./, (c) => c.toUpperCase())}: ${floorTxt(f.location)}`);
    const groups = new Map<string, Entity[]>();
    for (const o of overlay.occupants ?? []) {
      if (o.group_id && o.status === "contradicted") { groups.set(o.group_id, [...(groups.get(o.group_id) ?? []), o]); continue; }
      parts.push(`Occupants: ${occTxt(o)}, ${floorTxt(o.location)}`);
    }
    for (const g of groups.values()) parts.push(`Occupants: ${occTxt(g[0])} (contradicted: ${g.map((o) => floorTxt(o.location)).join(" / ")})`);
    for (const h of overlay.hazards ?? []) parts.push(`${(h.kind ?? "hazard").replace(/^./, (c) => c.toUpperCase())} (hazard): ${floorTxt(h.location)}`);
    incident = parts.length ? parts.join(" · ") : "Overlay received, nothing reported";
  }
  // sleeping areas, exits & stairs, scan
  const bedFloors = [...new Set(beds.map((b) => b.fi + 1))].sort();
  const sleeping = !plan ? NA : beds.length ? `${plural(beds.length, "bedroom")} · floor${bedFloors.length > 1 ? "s" : ""} ${bedFloors.join(", ")}` : "No bedrooms detected in the plan";
  const stairsTxt = !plan ? NA : floors.length > 1 ? (stairs.length ? `stairs: ${stairs.length}` : "stairs: not detected") : "single floor";
  const exits = !plan ? NA : `${plural(exitDoors.length, "exterior door")} · ${stairsTxt}`;
  const ratio = num(doc?.quality?.ratio_superficie);
  const scanPartial = ratio != null && ratio < 0.7;
  const scanCard = !plan ? "No interior scan on file" : `${created ? scanLabel.replace("Scan: ", "") : "date not available"} · plan/listing area ${ratio != null ? ratio.toFixed(2) : NA}`;

  // size-up: listing vs plan
  const planSqft = num(doc?.quality?.superficie_plano_sqft);
  const sizeUp: Row[] = [
    { label: "Use", value: rec?.mls_property_type ?? (L?.homeType ? `Residential, ${L.homeType}` : NA), detail: "listing", status: "verified" },
    { label: "Stories", value: `${rec?.stories ?? NA} (listing) · plan: ${plan ? plural(floors.length, "floor") : NA}` },
    { label: "Year built", value: L?.yearBuilt != null ? String(L.yearBuilt) : NA, detail: "listing" },
    { label: "Area", value: `${L?.sqft ? sqft(L.sqft) : NA} (listing) · plan: ${planSqft ? sqft(planSqft) : NA}` },
    { label: "Bedrooms", value: `${L?.beds ?? NA} (listing) · plan: ${plan ? beds.length : NA}` },
    { label: "Baths", value: `${L?.baths ?? NA}${rec?.baths_full != null ? ` (${rec.baths_full} full${rec.baths_half ? `, ${rec.baths_half} half` : ""})` : ""} (listing) · plan: ${plan ? plural(baths.length, "bathroom") : NA}` },
  ];
  // hazards from the listing text only
  const hazards: Row[] = rec
    ? listingMentions(rec).map((m) => ({ label: m.name, value: m.found ? "mentioned in listing" : "not mentioned in listing", detail: m.found ? `Listing: ${m.snippet}` : undefined, status: m.found ? ("verified" as const) : undefined }))
    : HAZARD_TERMS.map(([name]) => ({ label: name, value: "Not available (no listing text on file)" }));
  const unknowns = ["Utility shutoffs", "Street side", ...((overlay?.occupants ?? []).length ? [] : ["Occupants"]), "Roof type"];

  // considerations: brief (none yet for houses) + incident overlay; level only when there is evidence
  const level = (c: number, ev: string[]): HouseConsideration["level"] => (!ev.length ? null : c >= 0.7 ? "High" : c >= 0.4 ? "Medium" : "Low");
  const briefCons: Consideration[] = [];
  const considerations: HouseConsideration[] = [
    ...briefCons.map((c) => ({ id: c.id, text: c.title, rationale: c.rationale, level: level(c.confidence, c.evidence_ids), evidence: c.evidence_ids, refs: [], source: "brief" as const })),
    ...(overlay?.considerations ?? []).map((c) => ({ id: c.id, text: c.text, rationale: c.rationale, level: level(c.confidence, c.evidence_ids), evidence: c.evidence_ids, refs: c.refs, source: "incident" as const })),
  ];

  // Brief (existing type)
  const iso = now.toISOString();
  const fact = (key: string, value: unknown, status: Fact["status"], confidence: number, evidence_ids: string[]): Fact => ({ key, value, status, confidence, evidence_ids, as_of: null, updated_at: iso });
  const evidence: Record<string, Evidence> = {};
  if (L || rec) evidence.ev_listing = { id: "ev_listing", kind: "costar_record", source: "costar", locator: { sample: sample.id } };
  if (plan) evidence.ev_plan = { id: "ev_plan", kind: "scan_floorplan", source: "matterport", captured_at: created, locator: { model_id: sample.matterportModelId } };
  const size_up: Record<string, Fact> = {};
  if (L?.homeType) size_up["building.use"] = fact("building.use", L.homeType, "verified", 0.9, ["ev_listing"]);
  if (rec?.stories != null) size_up["building.stories"] = fact("building.stories", rec.stories, "verified", 0.9, ["ev_listing"]);
  if (L?.yearBuilt != null) size_up["building.year_built"] = fact("building.year_built", L.yearBuilt, "verified", 0.9, ["ev_listing"]);
  if (L?.sqft) size_up["building.area_sqft"] = fact("building.area_sqft", L.sqft, "verified", 0.9, ["ev_listing"]);
  if (plan) size_up["building.floors_scanned"] = fact("building.floors_scanned", floors.length, "verified", 0.9, ["ev_plan"]);
  const markers: Marker[] = [
    ...exitDoors.map((o) => ({ id: o.id, kind: "exit" as const, label: "Exterior door", floor: 1, status: "inferred" as const, confidence: 0.7, evidence_ids: ["ev_plan"] })),
    ...stairs.map((s) => ({ id: s.id, kind: "stair" as const, label: "Stairs", floor: 1, status: "inferred" as const, confidence: 0.6, evidence_ids: ["ev_plan"] })),
  ];
  const brief: Brief = {
    brief_id: `house-${sample.id}`, incident_id: overlay?.incident_id ?? "none", version: overlay?.version ?? 0, generated_at: iso,
    location_confirmed: false, freshness: { scan_age_days: created ? Math.floor((now.getTime() - Date.parse(created)) / 86400000) : null, costar_as_of: null },
    size_up, markers,
    hazards: rec ? listingMentions(rec).filter((m) => m.found).map((m) => fact(`hazard.${m.name.toLowerCase().replace(/ /g, "_")}`, "mentioned in listing", "verified", 0.8, ["ev_listing"])) : [],
    exposures: [], considerations: briefCons, changes_since_last: overlay?.changes_since_last ?? [], gaps: unknowns, evidence,
    disclaimer: "Advisory only. Verify on arrival.",
  };
  return {
    brief,
    extras: {
      header: { type, address: address || sample.address, incident: overlay ? `Incident ${overlay.incident_id} · v${overlay.version}` : null },
      scan: { label: scanLabel, created, months },
      cards: { incident, sleeping, exits, scan: scanCard, scanPartial },
      sizeUp, hazards, unknowns, considerations,
    },
  };
}
