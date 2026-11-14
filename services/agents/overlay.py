"""Incident overlay (docs/INCIDENT_OVERLAY_SPEC.md): deterministic parts. No LLM.

The incident_mapper subagent decides WHAT was reported and WHERE on the plan, as ids. This module:

- keeps the call transcript in the workspace (transcript.json), one span id per segment;
- loads a building's plan index (data/raw/<id>/derived/plan_index.json; missing = no interior scan);
- assembles an IncidentOverlay from the mapper's draft: version = brief version, reported_at from the
  spans, caller confidence capped, floor_reported mapped to a floor id only when the scan has that floor;
- checks it: Bruno's validate_overlay.py, plus our rules (evidence is caller spans, contradictions are
  candidates from different calls and never resolved, no room more precise than the words);
- diffs two versions into changes_since_last.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from pydantic import BaseModel

from flamescout_schemas import (CallSegment, Evidence, IncidentOverlay, OverlayBlocked, OverlayConsideration,
                                OverlayContradiction, OverlayFire, OverlayHazard, OverlayObjective,
                                OverlayOccupant, now)

from .brief import WINDOWLESS

if TYPE_CHECKING:
    from .workspace import Workspace

ROOT = Path(__file__).resolve().parents[2]
PLAN_INDEX_ROOT = ROOT / "data" / "raw"   # <building_id>/derived/plan_index.json, gitignored sponsor data
TRANSCRIPT = "transcript.json"
REPORTED_CAP = 0.6                        # a caller's report, as in fusion
EMPTY_INDEX = {"floors": [], "rooms": {}, "openings": {}, "stairs": []}

_spec = importlib.util.spec_from_file_location(
    "validate_overlay", ROOT / "packages" / "schemas" / "overlay" / "validate_overlay.py")
validate_overlay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_overlay)


def overlay_rel(version: int) -> str:
    return f"overlay_v{version}.json"


def dump(overlay: IncidentOverlay) -> str:
    """The JSON the viewer gets. No nulls: the validator reads "floor_id": null as a bad floor id."""
    return overlay.model_dump_json(indent=2, exclude_none=True)


def read(ws: "Workspace", version: int) -> Optional[IncidentOverlay]:
    text = ws.read_or_none(overlay_rel(version))
    return IncidentOverlay.model_validate_json(text) if text else None


# ---------- transcript ----------

class Span(BaseModel):
    span_id: str          # "<call_id>:<t_start>-<t_end>", e.g. "c1:42.0-51.5"
    call_id: str
    speaker: str
    text: str
    t_start: float
    t_end: float
    received_at: datetime


def span_id(seg: CallSegment) -> str:
    return f"{seg.call_id}:{seg.t_start:.1f}-{seg.t_end:.1f}"


def transcript(ws: "Workspace") -> list[Span]:
    text = ws.read_or_none(TRANSCRIPT)
    return [Span(**s) for s in json.loads(text)] if text else []


def append_transcript(ws: "Workspace", segments: Iterable[CallSegment]) -> None:
    spans = transcript(ws)
    known = {s.span_id for s in spans}
    new = [Span(span_id=span_id(seg), received_at=now(), **seg.model_dump())
           for seg in segments if span_id(seg) not in known]
    if new:
        ws.write(TRANSCRIPT, json.dumps([s.model_dump(mode="json") for s in spans + new], indent=2))


def span_evidence(spans: Iterable[Span]) -> dict[str, Evidence]:
    """Transcript spans as Evidence, so overlay evidence ids resolve in the brief of the same version."""
    return {s.span_id: Evidence(id=s.span_id, kind="transcript_span", source="call", captured_at=s.received_at,
                                locator={"call_id": s.call_id, "start": s.t_start, "end": s.t_end, "text": s.text})
            for s in spans}


# ---------- plan index ----------

def load_plan_index(building_id: Optional[str]) -> dict:
    """The building's plan index, or the empty one (no interior scan) when there is none or it is unreadable."""
    if not building_id or not re.fullmatch(r"[A-Za-z0-9_-]+", building_id):
        return dict(EMPTY_INDEX)
    path = PLAN_INDEX_ROOT / building_id / "derived" / "plan_index.json"
    try:
        index = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(EMPTY_INDEX)
    return {key: index.get(key, empty) for key, empty in EMPTY_INDEX.items()}


def has_scan(index: dict) -> bool:
    return bool(index.get("rooms"))


def floor_id_for(index: dict, floor_reported: Optional[int]) -> Optional[str]:
    """Floor n (ground = 1) -> its id, only when the scan covers it. The index lists floors bottom up."""
    floors = index.get("floors", [])
    return floors[floor_reported - 1] if floor_reported and 1 <= floor_reported <= len(floors) else None


# ---------- the mapper's draft ----------

class _DraftReport(BaseModel):
    """Code fills reported_at; the mapper leaves it out."""
    reported_at: Optional[datetime] = None


class DraftOccupant(_DraftReport, OverlayOccupant):
    pass


class DraftFire(_DraftReport, OverlayFire):
    pass


class DraftHazard(_DraftReport, OverlayHazard):
    pass


class DraftBlocked(_DraftReport, OverlayBlocked):
    pass


class OverlayDraft(BaseModel):
    """What the incident_mapper submits. Code adds incident_id, model_id, version, as_of, reported_at,
    changes_since_last and routes ([], the viewer's planner fills them)."""
    occupants: list[DraftOccupant] = []
    fire: list[DraftFire] = []
    hazards: list[DraftHazard] = []
    blocked: list[DraftBlocked] = []
    contradictions: list[OverlayContradiction] = []
    objectives: list[OverlayObjective] = []
    considerations: list[OverlayConsideration] = []


def _reported_at(evidence_ids: list[str], spans: dict[str, Span], default: datetime) -> datetime:
    times = [spans[e].received_at for e in evidence_ids if e in spans]
    return max(times) if times else default


def assemble(draft: OverlayDraft, incident_id: str, model_id: str, version: int, index: dict,
             spans: list[Span], previous: Optional[IncidentOverlay] = None) -> IncidentOverlay:
    by_id, as_of = {s.span_id: s for s in spans}, now()

    def report(entity: _DraftReport, cls):
        data = entity.model_dump(exclude={"reported_at"})
        data["reported_at"] = _reported_at(entity.evidence_ids, by_id, as_of)
        data["confidence"] = min(data["confidence"], REPORTED_CAP)
        loc = data.get("location")
        if loc and loc["kind"] == "floor" and not loc.get("floor_id"):
            loc["floor_id"] = floor_id_for(index, loc.get("floor_reported"))
        return cls.model_validate(data)

    overlay = IncidentOverlay(
        incident_id=incident_id, model_id=model_id, version=version, as_of=as_of,
        occupants=[report(o, OverlayOccupant) for o in draft.occupants],
        fire=[report(f, OverlayFire) for f in draft.fire],
        hazards=[report(h, OverlayHazard) for h in draft.hazards],
        blocked=[report(b, OverlayBlocked) for b in draft.blocked],
        contradictions=draft.contradictions, objectives=draft.objectives, considerations=draft.considerations,
        routes=[])
    overlay.changes_since_last = diff(previous, overlay) if previous else []
    return overlay


def fallback(incident_id: str, model_id: str, version: int, reason: str) -> IncidentOverlay:
    """Nothing mapped: an empty overlay that says why, so the viewer's version stays in step with the brief."""
    return IncidentOverlay(incident_id=incident_id, model_id=model_id, version=version, as_of=now(),
                           changes_since_last=[f"Overlay not available: {reason}"])


# ---------- checks ----------

# Words a caller uses for a room type (plan index "type" / "label"). Extra words only: the type's own
# words always count.
ROOM_WORDS = {
    "bedroom": {"bedroom", "bedrooms"}, "bathroom": {"bathroom", "bath", "toilet", "restroom", "washroom"},
    "kitchen": {"kitchen"}, "garage": {"garage"}, "living room": {"living", "lounge", "family room"},
    "dining room": {"dining"}, "hallway": {"hall", "hallway", "corridor", "landing"},
    "staircase": {"stairs", "stair", "staircase", "stairway", "stairwell"},
    "entrance": {"entrance", "entry", "foyer", "front door"}, "office": {"office", "study"},
    "laundry room": {"laundry", "utility room"}, "closet": {"closet"}, "basement": {"basement", "cellar"},
    "attic": {"attic"},
}
_GENERIC = {"room", "other", "area", "space"}


def _room_words(room: dict) -> set[str]:
    words: set[str] = set()
    for value in (room.get("type"), room.get("label")):
        if value:
            value = str(value).lower()
            words |= ROOM_WORDS.get(value, set()) | ({value} if value not in _GENERIC else set())
            words |= {w for w in re.findall(r"[a-z]+", value) if w not in _GENERIC and len(w) > 2}
    return words


def _mentions(text: str, words: set[str]) -> set[str]:
    text = text.lower()
    return {w for w in words if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text)}


def _locations(overlay: IncidentOverlay):
    for name in ("occupants", "fire", "hazards"):
        for e in getattr(overlay, name):
            yield name, e


def check(overlay: IncidentOverlay, index: dict, spans: list[Span]) -> list[str]:
    """Why this overlay cannot be published. Empty list = valid."""
    data = json.loads(dump(overlay))
    errors = list(validate_overlay.validate(data, index))
    callers = {s.span_id: s for s in spans if s.speaker == "caller"}

    def evidence(where: str, ids: list[str]) -> None:
        if unknown := [e for e in ids if e not in callers]:
            errors.append(f"{where}: evidence {unknown} are not caller spans from get_transcript()")

    reports = [*overlay.occupants, *overlay.fire, *overlay.hazards, *overlay.blocked]
    for e in reports:
        evidence(e.id, e.evidence_ids)
        if e.status == "verified":
            errors.append(f"{e.id}: a caller's report is never 'verified' (use 'reported')")
        if e.status == "contradicted" and not e.group_id:
            errors.append(f"{e.id}: a contradicted entity needs the group_id of its contradiction")
    for o in overlay.objectives:
        evidence(o.id, o.evidence_ids)
        if o.target.kind == "floor" and o.target.ref_id and o.target.ref_id not in index.get("floors", []):
            errors.append(f"{o.id}: target ref_id {o.target.ref_id!r} is not a floor id of the plan; "
                          "leave ref_id out and give floor_reported")
    for c in overlay.considerations:
        evidence(c.id, c.evidence_ids)
        if WINDOWLESS.search(f"{c.text} {c.rationale}"):
            errors.append(f"{c.id}: window findings from the plan are not used yet; leave them out")

    groups: dict[str, list] = {}
    for e in reports:
        if e.group_id:
            groups.setdefault(e.group_id, []).append(e)
    for g, members in groups.items():
        calls = {callers[ev].call_id for m in members for ev in m.evidence_ids if ev in callers}
        if len(calls) < 2:
            errors.append(f"group {g}: candidates must come from different calls (one caller changing their "
                          "mind is a correction, not a contradiction)")
    for c in overlay.contradictions:
        if c.resolved_by_dispatcher:
            errors.append(f"contradiction {c.group_id}: only the dispatcher resolves it; keep resolved_by_dispatcher false")

    rooms = index.get("rooms", {})
    for kind, e in _locations(overlay):
        loc = e.location
        room_id = loc.room_id if loc.kind == "room" else (loc.anchor.room_id if loc.kind == "anchor" else None)
        if room_id not in rooms:
            continue  # unknown ids are reported by the validator
        said = " ".join(callers[ev].text for ev in e.evidence_ids if ev in callers)
        named = _mentions(said, _room_words(rooms[room_id]))
        if not named:
            errors.append(f"{e.id}: the cited words do not name a {rooms[room_id].get('type') or 'room'}; "
                          f"use a floor or unknown location instead of {room_id}")
            continue
        floor = rooms[room_id].get("floor_id")
        on_floor = [rid for rid, r in rooms.items() if r.get("floor_id") == floor and named & _room_words(r)]
        if len(on_floor) > 1:
            errors.append(f"{e.id}: the plan has {len(on_floor)} rooms that match '{sorted(named)[0]}' on that floor "
                          f"({', '.join(sorted(on_floor))}) and the index cannot tell them apart; "
                          "use a floor location and put the caller's words in note")
    return errors


# ---------- changes_since_last ----------

def describe(loc) -> str:
    if loc.kind == "room":
        return f"in {loc.room_id}"
    if loc.kind == "anchor":
        a = loc.anchor
        where = a.position.replace("_", " ")
        return f"{where} {a.ref_id} in {a.room_id}" if a.ref_id else f"{where} of {a.room_id}"
    if loc.kind == "floor":
        return f"on floor {loc.floor_reported}" if loc.floor_reported is not None else f"on floor {loc.floor_id}"
    return "location unknown"


def _entities(overlay: IncidentOverlay) -> dict[str, tuple[str, object]]:
    out = {}
    for name in ("occupants", "fire", "hazards", "blocked"):
        for e in getattr(overlay, name):
            out[e.id] = (name, e)
    return out


def _label(name: str, e) -> str:
    if name == "occupants":
        n = e.count if e.count is not None else f"{e.count_range[0]}-{e.count_range[1]}"
        return f"occupants {e.id} ({n})"
    if name == "blocked":
        return f"blocked {e.id} ({e.ref_id})"
    return f"{name.rstrip('s') if name != 'fire' else 'fire'} {e.id} ({e.kind})"


def diff(previous: IncidentOverlay, current: IncidentOverlay) -> list[str]:
    """What changed between two overlay versions, by entity id. No LLM."""
    old, new, out = _entities(previous), _entities(current), []
    for eid, (name, e) in new.items():
        where = describe(e.location) if name != "blocked" else ""
        if eid not in old:
            out.append(f"New: {_label(name, e)} {where}".rstrip())
            continue
        _, o = old[eid]
        if name != "blocked" and describe(o.location) != where:
            out.append(f"{eid} moved: {describe(o.location)} -> {where}")
        if o.status != e.status:
            out.append(f"{eid} is now contradicted (group {e.group_id})" if e.status == "contradicted"
                       else f"{eid}: {o.status} -> {e.status}")
        if name == "occupants" and (o.count, o.count_range) != (e.count, e.count_range):
            out.append(f"{eid}: count {_label(name, o).split('(')[1][:-1]} -> {_label(name, e).split('(')[1][:-1]}")
    out += [f"No longer reported: {_label(*old[eid])}" for eid in old if eid not in new]
    was = {c.group_id: c for c in previous.contradictions}
    for c in current.contradictions:
        if c.group_id not in was:
            out.append(f"New contradiction {c.group_id}: {c.key}")
        elif c.resolved_by_dispatcher and not was[c.group_id].resolved_by_dispatcher:
            out.append(f"Contradiction {c.group_id} resolved by the dispatcher")
    old_obj = {o.id: o for o in previous.objectives}

    def goal(o) -> str:
        return f"{o.type} {o.target.kind} {o.target.ref_id or f'floor {o.target.floor_reported}'}"

    for o in current.objectives:
        if o.id not in old_obj:
            out.append(f"New objective {o.id}: {goal(o)} (priority {o.priority})")
            continue
        if goal(old_obj[o.id]) != goal(o):
            out.append(f"Objective {o.id}: {goal(old_obj[o.id])} -> {goal(o)}")
        if old_obj[o.id].priority != o.priority:
            out.append(f"Objective {o.id}: priority {old_obj[o.id].priority} -> {o.priority}")
    out += [f"Objective dropped: {oid}" for oid in old_obj if oid not in {o.id for o in current.objectives}]
    return out


def overlay_ids(overlay: Optional[IncidentOverlay]) -> set[str]:
    """Ids a brief consideration may put in refs: entities, contradiction groups, objectives, considerations."""
    if overlay is None:
        return set()
    return ({*_entities(overlay), *(c.group_id for c in overlay.contradictions),
             *(o.id for o in overlay.objectives), *(c.id for c in overlay.considerations)})


def cited_spans(overlay: Optional[IncidentOverlay], spans: list[Span]) -> list[Span]:
    if overlay is None:
        return []
    cited = {ev for e in [*overlay.occupants, *overlay.fire, *overlay.hazards, *overlay.blocked,
                          *overlay.objectives, *overlay.considerations] for ev in e.evidence_ids}
    return [s for s in spans if s.span_id in cited]
