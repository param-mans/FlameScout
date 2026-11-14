"""FlameScout shared schemas (PRD Section 7, trimmed for the MVP).

Every cross-module payload is one of these models. The frontend gets
TypeScript types from the JSON Schema: run `python scripts/export_schemas.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

FactStatus = Literal["verified", "inferred", "reported", "unverified", "contradicted"]
# verified = record or deterministic computation on records
# inferred = vision / model / heuristic
# reported = from a caller
# unverified = shown but not supported
# contradicted = conflicting sources


def now() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(BaseModel):
    id: str
    kind: Literal["scan_frame", "scan_floorplan", "aerial_crop",
                  "costar_record", "transcript_span", "rule_output"]
    source: Literal["matterport", "vexcel", "costar", "call", "rules"]
    uri: Optional[str] = None
    captured_at: Optional[datetime] = None
    # {"call_id": "c1", "start": 3.2, "end": 7.9, "text": "..."} or {"sweep_id": ..., "bbox": [x, y, w, h]}
    locator: Optional[dict] = None


class Fact(BaseModel):
    key: str                      # e.g. "building.stories", "incident.floor"
    value: Any
    status: FactStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = []
    as_of: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=now)


# ---------- Incident (calls) ----------

class CallSegment(BaseModel):
    call_id: str
    speaker: Literal["call_taker", "caller"]
    text: str
    t_start: float
    t_end: float


class ContradictionValue(BaseModel):
    call_id: str
    value: Any
    span: dict  # {"start": float, "end": float, "text": str}


class Contradiction(BaseModel):
    key: str
    values: list[ContradictionValue]
    resolved_by_dispatcher: bool = False


class IncidentRecord(BaseModel):
    incident_id: str
    status: Literal["listening", "locating", "confirmed", "briefing", "updated"] = "listening"
    call_ids: list[str] = []
    facts: dict[str, Fact] = {}
    evidence: dict[str, Evidence] = {}
    contradictions: list[Contradiction] = []
    open_questions: list[str] = []
    confirmed_building_id: Optional[str] = None
    ready_to_brief: bool = False
    created_at: datetime = Field(default_factory=now)


class LocationCandidate(BaseModel):
    """A building the locator thinks the callers mean. Dispatcher must confirm one."""
    building_id: str
    name: Optional[str] = None
    address: str
    coordinates: dict = {}               # {"lat":..,"lon":..}
    score: float = Field(ge=0, le=1)
    match_reasons: list[str] = []
    scan_available: Optional[bool] = None


class CopilotSuggestion(BaseModel):
    """L1 copilot: a question the dispatcher could ask next (FR-11)."""
    id: str                              # "<incident_id>--<field_slug>"
    incident_id: str
    field: str                           # fact key the question would fill
    question: str
    reason: Literal["missing", "contradiction"] = "missing"
    status: Literal["open", "dismissed", "asked"] = "open"


class CrewFeedback(BaseModel):
    """Officer marks a fact correct / wrong / outdated (FR-15)."""
    id: str
    incident_id: str
    building_id: Optional[str] = None
    fact_key: str
    verdict: Literal["correct", "wrong", "outdated"]
    note: Optional[str] = None
    role: str = "officer"
    created_at: datetime = Field(default_factory=now)


EventType = Literal["transcript.segment", "incident.updated", "contradiction.raised",
                    "location.candidates", "dossier.progress", "brief.updated",
                    "copilot.suggestion", "metric.tick"]


class Event(BaseModel):
    """Envelope for every WebSocket message on /ws/incidents/{id}."""
    type: EventType
    incident_id: str
    ts: datetime = Field(default_factory=now)
    trace_id: Optional[str] = None
    payload: dict = {}


# ---------- Dossier and brief ----------

class Marker(BaseModel):
    id: str
    kind: Literal["entry", "stair", "exit", "elevator", "key_box",
                  "roof_access", "fdc", "standpipe", "other"]
    label: str
    floor: Optional[int] = None
    scan_position: Optional[dict] = None  # {"x":..,"y":..,"z":..} model space
    map_position: Optional[dict] = None   # {"lat":..,"lon":..}
    status: FactStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = []


class BuildingDossier(BaseModel):
    building_id: str
    address: str
    coordinates: dict
    facts: dict[str, Fact] = {}          # building.use, building.stories, building.year_built ...
    tenants: list[Fact] = []
    markers: list[Marker] = []
    exposures: list[dict] = []           # adjacent structures with distance + evidence_ids
    evidence: dict[str, Evidence] = {}
    scan_available: bool = False
    scan_captured_at: Optional[datetime] = None
    matterport_model_id: Optional[str] = None
    generated_at: datetime = Field(default_factory=now)


class Consideration(BaseModel):
    id: str
    rank: int
    category: Literal["access", "life_safety", "exposure", "hazard",
                      "roof", "staging", "water_supply"]
    title: str           # advisory: "Consider ..."
    rationale: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = []


class Brief(BaseModel):
    brief_id: str
    incident_id: str
    building_id: Optional[str] = None
    version: int
    generated_at: datetime = Field(default_factory=now)
    location_confirmed: bool
    freshness: dict = {}                 # {"scan_age_days": int|None, "costar_as_of": str|None}
    size_up: dict[str, Fact] = {}
    markers: list[Marker] = []
    hazards: list[Fact] = []
    exposures: list[dict] = []
    considerations: list[Consideration] = Field(default_factory=list, max_length=5)
    changes_since_last: list[str] = []
    gaps: list[str] = []
    evidence: dict[str, Evidence] = {}   # so the UI evidence drawer can resolve every id
    crew_notes: list[CrewFeedback] = []  # earlier crew feedback on this building
    disclaimer: str = "Advisory only. Verify on arrival."
