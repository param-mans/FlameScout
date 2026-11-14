// Hand-mirrored from packages/schemas/flamescout_schemas/__init__.py.
// The exported schema.json has no root type, so json-schema-to-typescript emits nothing useful.
// If you change a Pydantic model, update this file too.

export type FactStatus = "verified" | "inferred" | "reported" | "unverified" | "contradicted";

export interface Evidence {
  id: string;
  kind: "scan_frame" | "scan_floorplan" | "aerial_crop" | "costar_record" | "transcript_span" | "rule_output";
  source: "matterport" | "vexcel" | "costar" | "call" | "rules";
  uri?: string | null;
  captured_at?: string | null;
  locator?: Record<string, unknown> | null;
}

export interface Fact {
  key: string;
  value: unknown;
  status: FactStatus;
  confidence: number;
  evidence_ids: string[];
  as_of?: string | null;
  updated_at: string;
}

export interface CallSegment {
  call_id: string;
  speaker: "call_taker" | "caller";
  text: string;
  t_start: number;
  t_end: number;
}

export interface ContradictionValue {
  call_id: string;
  value: unknown;
  span: { start: number; end: number; text?: string };
}

export interface Contradiction {
  key: string;
  values: ContradictionValue[];
  resolved_by_dispatcher: boolean;
}

export interface IncidentRecord {
  incident_id: string;
  status: "listening" | "locating" | "confirmed" | "briefing" | "updated";
  call_ids: string[];
  facts: Record<string, Fact>;
  evidence: Record<string, Evidence>;
  contradictions: Contradiction[];
  open_questions: string[];
  confirmed_building_id?: string | null;
  ready_to_brief: boolean;
}

export type MarkerKind = "entry" | "stair" | "exit" | "elevator" | "key_box" | "roof_access" | "fdc" | "standpipe" | "other";

export interface Marker {
  id: string;
  kind: MarkerKind;
  label: string;
  floor?: number | null;
  scan_position?: { x: number; y: number; z: number } | null;
  map_position?: { lat: number; lon: number } | null;
  status: FactStatus;
  confidence: number;
  evidence_ids: string[];
}

export type ConsiderationCategory = "access" | "life_safety" | "exposure" | "hazard" | "roof" | "staging" | "water_supply";

export interface Consideration {
  id: string;
  rank: number;
  category: ConsiderationCategory;
  title: string;
  rationale: string;
  confidence: number;
  evidence_ids: string[];
}

export interface Brief {
  brief_id: string;
  incident_id: string;
  version: number;
  generated_at: string;
  location_confirmed: boolean;
  freshness: { scan_age_days?: number | null; costar_as_of?: string | null };
  size_up: Record<string, Fact>;
  markers: Marker[];
  hazards: Fact[];
  exposures: Record<string, unknown>[];
  considerations: Consideration[];
  changes_since_last: string[];
  gaps: string[];
  evidence: Record<string, Evidence>;
  disclaimer: string;
}

// Not in the Python schemas yet: UI-only shape for location candidates (PRD FR-3).
export interface BuildingCandidate {
  building_id: string;
  name: string;
  address: string;
  score: number;
}
