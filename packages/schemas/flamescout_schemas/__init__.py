"""FlameScout shared schemas (PRD Section 7, trimmed for the MVP).

Every cross-module payload is one of these models. The frontend gets
TypeScript types from the JSON Schema: run `python scripts/export_schemas.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
                  "costar_record", "transcript_span", "rule_output", "crew_feedback"]
    source: Literal["matterport", "vexcel", "costar", "call", "rules", "crew"]
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
    reason: Literal["missing", "contradiction", "ambiguous_place"] = "missing"
    status: Literal["open", "dismissed", "asked"] = "open"
    room_ids: list[str] = Field(default_factory=list)      # ambiguous_place: the plan rooms that match
    evidence_ids: list[str] = Field(default_factory=list)  # ambiguous_place: the caller spans it is about


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
                    "copilot.suggestion", "metric.tick", "overlay.updated", "call.status", "calls.cleared"]


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


class DataContradictionValue(BaseModel):
    layer: Literal["costar", "matterport", "vexcel"]
    value: Any
    evidence_ids: list[str] = []


class DataContradiction(BaseModel):
    """Two data layers disagree (e.g. the listing's bedrooms vs the scan's). Never resolved by code."""
    key: str                             # the listing fact, e.g. "building.bedrooms"
    message: str                         # "Listing: 4 bedrooms; scan: 5. Verify occupant count."
    values: list[DataContradictionValue]
    status: Literal["contradicted"] = "contradicted"


class BuildingDossier(BaseModel):
    building_id: str
    address: str
    coordinates: dict
    facts: dict[str, Fact] = {}          # building.use, building.stories, building.year_built ...
    tenants: list[Fact] = []
    markers: list[Marker] = []
    exposures: list[dict] = []           # adjacent structures with distance + evidence_ids
    evidence: dict[str, Evidence] = {}
    contradictions: list[DataContradiction] = []   # between data layers, computed in code
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
    cross_layer: bool = False   # evidence from 2+ of the costar / matterport / vexcel layers
    refs: list[str] = []        # ids in the incident overlay of the same version (IncidentOverlay)


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
    changes_since_last: list[str] = []   # deterministic diff against the previous version
    gaps: list[str] = []
    verifier_rejections: int = 0         # draft considerations rejected (code checks + critic) for this version
    evidence: dict[str, Evidence] = {}   # so the UI evidence drawer can resolve every id
    crew_notes: list[CrewFeedback] = []  # earlier crew feedback on this building
    published: bool = False              # false while low-confidence considerations await the dispatcher
    partial: bool = False                # the run deadline (RUN_DEADLINE_S) cut this version short
    partial_reason: Optional[str] = None  # why, e.g. "run deadline of 90 s reached during resume; ..."
    data_contradictions: list[DataContradiction] = []  # data layers that disagree (listing vs scan)
    disclaimer: str = "Advisory only. Verify on arrival."


# ---------- Orchestrator progress (workspace/<incident_id>/todos.json) ----------

class TodoItem(BaseModel):
    content: str
    status: Literal["pending", "in_progress", "completed"]


class BuildingCandidate(LocationCandidate):
    """A building the dispatcher can confirm (PRD FR-3): the orchestrator's candidates.

    The same fields as LocationCandidate (the rules pipeline's candidates), so the UI reads both the
    same way. Filled deterministically by locator.find_candidates, never by an LLM.
    """


class Progress(BaseModel):
    """The orchestrator's write_todos plan, rewritten on every change. UI progress strip."""
    incident_id: str
    status: Literal["running", "awaiting_confirmation", "awaiting_approval", "done", "rejected"] = "running"
    version: int = 1                      # brief version being produced
    candidates: list[BuildingCandidate] = []   # what the dispatcher is choosing between
    todos: list[TodoItem] = []
    updated_at: datetime = Field(default_factory=now)
    trace_run_id: Optional[str] = None   # LangSmith run id of the orchestrator run


# ---------- Incident overlay (docs/INCIDENT_OVERLAY_SPEC.md) ----------
#
# What the calls reported, placed on the building plan by ids, for the 3D/2D viewer. Agents never
# output coordinates: extra fields are refused. The stdlib validator the viewer also runs is
# packages/schemas/overlay/validate_overlay.py; dump with model_dump(mode="json", exclude_none=True)
# (a null floor_id is an invalid floor id to it).

AnchorPosition = Literal["center", "near_door", "near_window", "near_stairs"]  # no orientation (plan index v2)


class _OverlayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Anchor(_OverlayModel):
    room_id: str
    position: AnchorPosition
    ref_id: Optional[str] = None         # opening (near_door / near_window) or stairs (near_stairs)

    @model_validator(mode="after")
    def _needs_ref(self) -> "Anchor":
        if self.position.startswith("near_") and not self.ref_id:
            raise ValueError(f"anchor.position {self.position!r} needs ref_id")
        return self


class OverlayLocation(_OverlayModel):
    """room > anchor > floor > unknown. Never a more precise kind than the words support."""
    kind: Literal["room", "anchor", "floor", "unknown"]
    room_id: Optional[str] = None
    anchor: Optional[Anchor] = None
    floor_id: Optional[str] = None       # only when the scan covers that floor
    floor_reported: Optional[int] = None  # the caller's floor number, ground floor = 1

    @model_validator(mode="after")
    def _fields_for_kind(self) -> "OverlayLocation":
        if self.kind == "room" and not self.room_id:
            raise ValueError("location kind 'room' needs room_id")
        if self.kind == "anchor" and self.anchor is None:
            raise ValueError("location kind 'anchor' needs anchor")
        if self.kind == "floor" and self.floor_id is None and self.floor_reported is None:
            raise ValueError("location kind 'floor' needs floor_id or floor_reported")
        if self.kind == "unknown" and (self.room_id or self.anchor):
            raise ValueError("location kind 'unknown' must not carry a room or an anchor")
        return self


class _OverlayReport(_OverlayModel):
    """Common to occupants, fire, hazards and blocked."""
    id: str
    status: FactStatus                   # a caller alone never makes it "verified"
    confidence: float = Field(ge=0, le=1)
    reported_at: datetime
    evidence_ids: list[str] = []         # transcript spans; empty only when status is "unverified"
    note: Optional[str] = None
    group_id: Optional[str] = None       # candidates of one contradiction share it (all "contradicted")

    @model_validator(mode="after")
    def _evidence(self) -> "_OverlayReport":
        if not self.evidence_ids and self.status != "unverified":
            raise ValueError(f"{self.id}: evidence_ids is required unless status is 'unverified'")
        return self


class OverlayOccupant(_OverlayReport):
    count: Optional[int] = Field(default=None, ge=1)
    count_range: Optional[list[int]] = None   # [min, max]
    location: OverlayLocation

    @model_validator(mode="after")
    def _count(self) -> "OverlayOccupant":
        r = self.count_range
        if self.count is None and not (r and len(r) == 2 and 1 <= r[0] <= r[1]):
            raise ValueError(f"{self.id}: needs count (>= 1) or count_range [min, max] with 1 <= min <= max")
        return self


class OverlayFire(_OverlayReport):
    kind: Literal["smoke", "fire", "heat"]
    location: OverlayLocation


class OverlayHazard(_OverlayReport):
    kind: Literal["gas", "chemical", "battery", "electrical", "structural", "other"]
    location: OverlayLocation


class OverlayBlocked(_OverlayReport):
    ref_id: str                          # a door, stairs or room reported impassable


class OverlayContradiction(_OverlayModel):
    group_id: str
    key: str                             # e.g. "occupants.location"
    resolved_by_dispatcher: bool = False


class ObjectiveTarget(_OverlayModel):
    kind: Literal["occupant", "occupant_group", "room", "floor"]
    ref_id: Optional[str] = None
    floor_reported: Optional[int] = None  # kind "floor" only, instead of ref_id

    @model_validator(mode="after")
    def _ref(self) -> "ObjectiveTarget":
        if self.kind != "floor":
            self.floor_reported = None  # redundant next to an occupant / group / room ref_id: dropped
        if not self.ref_id and not (self.kind == "floor" and self.floor_reported is not None):
            raise ValueError(f"target kind {self.kind!r} needs ref_id" +
                             (" or floor_reported" if self.kind == "floor" else ""))
        return self


class OverlayObjective(_OverlayModel):
    id: str
    type: Literal["rescue", "recon"]
    priority: int = Field(ge=1)
    target: ObjectiveTarget
    rationale: str
    evidence_ids: list[str] = Field(min_length=1)


class OverlayConsideration(_OverlayModel):
    id: str
    text: str                            # advisory: "Consider ..."
    rationale: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    refs: list[str] = []                 # ids in this overlay

    @model_validator(mode="after")
    def _advisory(self) -> "OverlayConsideration":
        if not self.text.startswith("Consider"):
            raise ValueError(f"{self.id}: text must start with 'Consider'")
        return self


class RouteWaypoint(_OverlayModel):
    kind: Literal["exterior", "room", "opening", "stairs"]
    ref_id: str


class OverlayRoute(_OverlayModel):
    """Planner output only (the viewer's deterministic planner). Agents always send routes: []."""
    id: str
    objective_id: str
    produced_by: Literal["planner"]
    kind: str
    status: FactStatus = "inferred"
    confidence: float = Field(ge=0, le=1)
    waypoints: list[RouteWaypoint] = Field(min_length=2)
    length_m_estimate: Optional[float] = None
    passes_near_fire: Optional[bool] = None
    note: Optional[str] = None


class IncidentOverlay(_OverlayModel):
    """One per brief version: overlay_vN.json, version == Brief.version."""
    incident_id: str
    model_id: str
    version: int = Field(ge=1)
    as_of: datetime
    changes_since_last: list[str] = []
    occupants: list[OverlayOccupant] = []
    fire: list[OverlayFire] = []
    hazards: list[OverlayHazard] = []
    blocked: list[OverlayBlocked] = []
    contradictions: list[OverlayContradiction] = []
    objectives: list[OverlayObjective] = []
    considerations: list[OverlayConsideration] = []
    routes: list[OverlayRoute] = []
