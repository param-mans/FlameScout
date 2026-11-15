// Hand-mirrored from packages/schemas/flamescout_schemas/__init__.py.
// The exported schema.json has no root type, so json-schema-to-typescript emits nothing useful.
// If you change a Pydantic model, update this file too.

export type FactStatus = "verified" | "inferred" | "reported" | "unverified" | "contradicted";

export interface Evidence {
  id: string;
  kind: "scan_frame" | "scan_floorplan" | "aerial_crop" | "costar_record" | "transcript_span" | "rule_output" | "crew_feedback" | "camera_frame";
  source: "matterport" | "vexcel" | "costar" | "call" | "rules" | "crew" | "camera";
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
  /** camera: an observation read from a frame of the calling device's camera (call_id cam1, ...) */
  speaker: "call_taker" | "caller" | "camera";
  text: string;
  t_start: number;
  t_end: number;
}

/** What the extractor understood from one caller line (transcript.segment `extracted`). */
export interface ExtractedFact {
  key: string;
  value: unknown;
  quote: string;
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
  building_id?: string | null;
  /** false while a low-confidence brief waits for the dispatcher's review. */
  published?: boolean;
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

export interface BuildingCandidate {
  building_id: string;
  name?: string | null;
  address: string;
  score: number;
  coordinates?: { lat?: number; lon?: number };
  match_reasons?: string[];
  scan_available?: boolean | null;
}

export interface CopilotSuggestion {
  id: string;
  incident_id: string;
  field: string;
  question: string;
  reason: "missing" | "contradiction";
  status: "open" | "dismissed" | "asked";
}

export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

/** The orchestrator's plan (dossier.progress events). */
export interface Progress {
  incident_id: string;
  status: "running" | "awaiting_confirmation" | "awaiting_approval" | "done" | "rejected";
  version: number;
  todos: TodoItem[];
}

/** WebSocket envelope on /ws/incidents/{id}. */
export interface ApiEvent {
  type: string;
  incident_id: string;
  ts: string;
  trace_id?: string | null;
  snapshot?: boolean;
  payload: Record<string, unknown>;
}
