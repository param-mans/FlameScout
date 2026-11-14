// Mock data modelled on scenario S2 (docs/PRD.md Appendix A). Replace with API/WebSocket data later.
import type { Brief, BuildingCandidate, CallSegment, Evidence, Fact, FactStatus, IncidentRecord } from "../types/schemas";

const T = "2026-11-14T12:00:00Z";
const fact = (key: string, value: unknown, status: FactStatus, confidence: number, evidence_ids: string[] = []): Fact => ({
  key, value, status, confidence, evidence_ids, updated_at: T,
});

export const segments: CallSegment[] = [
  { call_id: "c1", speaker: "call_taker", text: "911, what's the address of your emergency?", t_start: 0.0, t_end: 2.1 },
  { call_id: "c1", speaker: "caller", text: "There's smoke coming out of the building at 1200 Wilshire Boulevard, I think the third floor.", t_start: 2.4, t_end: 7.9 },
  { call_id: "c1", speaker: "call_taker", text: "Is anyone inside?", t_start: 8.1, t_end: 9.0 },
  { call_id: "c1", speaker: "caller", text: "I saw people at the windows.", t_start: 9.2, t_end: 10.8 },
  { call_id: "c2", speaker: "call_taker", text: "911, what's the address of your emergency?", t_start: 14.0, t_end: 16.0 },
  { call_id: "c2", speaker: "caller", text: "I'm across the street from the Wilshire Tower. Fire's on the fourth floor, flames at the corner window.", t_start: 16.3, t_end: 22.0 },
  { call_id: "c2", speaker: "caller", text: "Someone's shouting from the back stairs.", t_start: 22.2, t_end: 24.5 },
];

const evidence: Record<string, Evidence> = {
  e_c1: { id: "e_c1", kind: "transcript_span", source: "call", locator: { call_id: "c1", start: 2.4, end: 7.9, text: "…smoke coming out of the building… I think the third floor." } },
  e_c2: { id: "e_c2", kind: "transcript_span", source: "call", locator: { call_id: "c2", start: 16.3, end: 22.0, text: "Fire's on the fourth floor, flames at the corner window." } },
  e_costar: { id: "e_costar", kind: "costar_record", source: "costar", captured_at: "2026-10-01T00:00:00Z" },
  e_scan1: { id: "e_scan1", kind: "scan_floorplan", source: "matterport", captured_at: "2025-09-10T00:00:00Z", locator: { sweep_id: "demo", bbox: [120, 80, 40, 40] } },
  e_rule: { id: "e_rule", kind: "rule_output", source: "rules" },
};

export const candidates: BuildingCandidate[] = [
  { building_id: "hero_a", name: "Wilshire Tower", address: "1200 Wilshire Blvd, Los Angeles, CA", score: 0.94 },
  { building_id: "hero_b", name: "Wilshire Plaza", address: "1250 Wilshire Blvd, Los Angeles, CA", score: 0.61 },
];

export const incident: IncidentRecord = {
  incident_id: "inc_demo",
  status: "locating",
  call_ids: ["c1", "c2"],
  evidence,
  facts: {
    "location.address": fact("location.address", "1200 Wilshire Boulevard", "reported", 0.7, ["e_c1"]),
    "incident.floor": fact("incident.floor", "3 or 4", "contradicted", 0.3, ["e_c1", "e_c2"]),
    "people.reported": fact("people.reported", "People at windows; someone on back stairs", "reported", 0.6, ["e_c1", "e_c2"]),
    "smoke.visible": fact("smoke.visible", true, "reported", 0.7, ["e_c1"]),
    "alarm.sounding": fact("alarm.sounding", null, "unverified", 0, []),
  },
  contradictions: [
    {
      key: "incident.floor",
      resolved_by_dispatcher: false,
      values: [
        { call_id: "c1", value: 3, span: { start: 2.4, end: 7.9, text: "I think the third floor." } },
        { call_id: "c2", value: 4, span: { start: 16.3, end: 22.0, text: "Fire's on the fourth floor." } },
      ],
    },
  ],
  open_questions: ["Is anyone trapped? Which floor?", "Is the fire alarm sounding?", "Any hazardous materials mentioned?"],
  confirmed_building_id: null,
  ready_to_brief: true,
};

const baseBrief: Brief = {
  brief_id: "b1",
  incident_id: "inc_demo",
  version: 1,
  generated_at: T,
  location_confirmed: true,
  freshness: { scan_age_days: 430, costar_as_of: "Oct 2026" },
  evidence,
  size_up: {
    "building.use": fact("building.use", "Commercial office, mid-rise", "verified", 0.95, ["e_costar"]),
    "building.stories": fact("building.stories", 12, "verified", 0.95, ["e_costar"]),
    "building.year_built": fact("building.year_built", 1974, "verified", 0.95, ["e_costar"]),
    "building.area_sqft": fact("building.area_sqft", "186,000", "verified", 0.9, ["e_costar"]),
    "building.height_est": fact("building.height_est", "≈ 42 m (estimate)", "inferred", 0.6, ["e_rule"]),
    "incident.floor": fact("incident.floor", 3, "reported", 0.6, ["e_c1"]),
  },
  markers: [
    { id: "m1", kind: "stair", label: "Stair A (front)", floor: 3, status: "inferred", confidence: 0.82, evidence_ids: ["e_scan1"] },
    { id: "m2", kind: "stair", label: "Stair B (rear)", floor: 3, status: "inferred", confidence: 0.71, evidence_ids: ["e_scan1"] },
    { id: "m3", kind: "entry", label: "Main lobby entry", floor: 1, status: "verified", confidence: 0.9, evidence_ids: ["e_scan1"] },
    { id: "m4", kind: "roof_access", label: "Roof hatch", status: "unverified", confidence: 0.3, evidence_ids: [] },
  ],
  hazards: [
    fact("hazard.kitchen", "Restaurant tenant: cooking/grease fire potential", "inferred", 0.6, ["e_costar", "e_rule"]),
    fact("hazard.lithium", "Electronics retail tenant: lithium-ion batteries", "inferred", 0.5, ["e_costar", "e_rule"]),
  ],
  exposures: [{ side: "East", distance_m: 4, band: "close" }, { side: "North", distance_m: 18, band: "moderate" }],
  considerations: [
    { id: "c1", rank: 1, category: "life_safety", title: "Consider confirming the fire floor before committing to a stairwell", rationale: "Callers disagree on floor (3 vs 4).", confidence: 0.8, evidence_ids: ["e_c1", "e_c2"] },
    { id: "c2", rank: 2, category: "access", title: "Consider the front lobby entry for initial access", rationale: "Scan shows lobby entry and Stair A adjacent to it.", confidence: 0.75, evidence_ids: ["e_scan1"] },
    { id: "c3", rank: 3, category: "exposure", title: "Consider the close exposure on the east side", rationale: "Adjacent structure about 4 m away.", confidence: 0.7, evidence_ids: ["e_rule"] },
  ],
  changes_since_last: [],
  gaps: ["Sprinklers", "Standpipe", "Key box"],
  disclaimer: "Advisory only. Verify on arrival.",
};

export const briefV1: Brief = baseBrief;
export const briefV2: Brief = {
  ...baseBrief,
  brief_id: "b2",
  version: 2,
  size_up: { ...baseBrief.size_up, "incident.floor": fact("incident.floor", "3 or 4", "contradicted", 0.3, ["e_c1", "e_c2"]) },
  changes_since_last: ["Floor changed 3 → 4, reported by Call 2", "Rear stairwell occupant reported by Call 2"],
};

// No-scan variant (S4): interior sections show gaps, never invented data.
export const briefNoScan: Brief = {
  ...baseBrief,
  brief_id: "b4",
  markers: [],
  freshness: { scan_age_days: null, costar_as_of: "Oct 2026" },
  gaps: ["No interior scan on file", "Stairs", "Exits", "Sprinklers", "Standpipe", "Key box"],
  considerations: baseBrief.considerations.filter((c) => c.category !== "access"),
};

// UI-only placeholder layout for the SVG site plan (viewBox 0 0 800 520). Real positions come from
// marker.map_position / scan_position once the dossier supplies them.
export const markerXY: Record<string, [number, number]> = {
  m1: [405, 312], // Stair A (front)
  m2: [440, 188], // Stair B (rear)
  m3: [360, 334], // Main lobby entry
  m4: [340, 250], // Roof hatch
};
