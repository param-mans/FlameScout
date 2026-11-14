"""L1 dispatcher copilot (PRD FR-11): suggest the next question from gaps. Plain function.

Suggestions appear for missing high-value fields and unresolved contradictions,
and disappear as soon as the field is filled / the contradiction resolved.
Dismissed / asked state is kept by the caller (service) and passed in.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Optional, Sequence

from flamescout_schemas import Contradiction, CopilotSuggestion, IncidentRecord

from .fusion import QUESTIONS

if TYPE_CHECKING:
    from .workspace import Workspace


def suggestion_id(incident_id: str, field: str) -> str:
    return f"{incident_id}--{field.replace('.', '_')}"


def split_suggestion_id(sid: str) -> tuple[str, str]:
    incident_id, _, slug = sid.rpartition("--")
    return incident_id, slug


def suggestions(rec: IncidentRecord, states: dict[str, str]) -> list[CopilotSuggestion]:
    out: list[CopilotSuggestion] = []
    for c in rec.contradictions:
        if c.resolved_by_dispatcher:
            continue
        vals = " or ".join(f"{v.value} ({v.call_id})" for v in c.values)
        sid = suggestion_id(rec.incident_id, c.key)
        out.append(CopilotSuggestion(id=sid, incident_id=rec.incident_id, field=c.key,
                                     question=f"Callers disagree on {c.key}: {vals}. Can you confirm?",
                                     reason="contradiction", status=states.get(sid, "open")))  # type: ignore[arg-type]
    for key, q in QUESTIONS.items():
        if key in rec.facts:
            continue
        sid = suggestion_id(rec.incident_id, key)
        out.append(CopilotSuggestion(id=sid, incident_id=rec.incident_id, field=key, question=q,
                                     status=states.get(sid, "open")))  # type: ignore[arg-type]
    return out


# ---------- questions the orchestrator plans: unresolved life-safety contradictions ----------
#
# When callers contradict each other on where a crew goes first (the fire floor, whether anyone is
# trapped), the orchestrator adds a todo and asks the dispatcher. The wording is composed here from
# the contradiction record, so the values are always the callers' own. The questions are kept in
# the incident's workspace as a list of CopilotSuggestion. Not in copilot.json: that file is the
# API's {suggestion_id: status} map.

QUESTIONS_FILE = "copilot_questions.json"
LIFE_SAFETY_KEYS = ("incident.floor", "people.trapped")
_TOPIC = {"incident.floor": "floor", "people.trapped": "whether anyone is trapped"}
_ASK = {"incident.floor": "which floor are the flames or smoke on?",
        "people.trapped": "is anyone trapped or unaccounted for, and where?"}


def _said(value) -> str:
    return {True: "yes", False: "no"}[value] if isinstance(value, bool) else str(value)


def unresolved_life_safety(rec: IncidentRecord) -> list[Contradiction]:
    return [c for c in rec.contradictions if c.key in LIFE_SAFETY_KEYS and not c.resolved_by_dispatcher]


def question_for(c: Contradiction) -> str:
    values = " vs ".join(_said(v.value) for v in c.values)
    return f"Callers disagree on {_TOPIC[c.key]} ({values}). Ask: {_ASK[c.key]}"


def read_questions(ws: "Workspace") -> list[CopilotSuggestion]:
    text = ws.read_or_none(QUESTIONS_FILE)
    return [CopilotSuggestion(**q) for q in json.loads(text)] if text else []


def write_questions(ws: "Workspace", rec: IncidentRecord,
                    keys: Optional[Sequence[str]] = None) -> list[CopilotSuggestion]:
    """Add or refresh a question for each unresolved life-safety contradiction (optionally only `keys`).
    Returns the questions written; the file is left alone when there are none."""
    wanted = [c for c in unresolved_life_safety(rec) if keys is None or c.key in keys]
    if not wanted:
        return []
    by_id = {q.id: q for q in read_questions(ws)}
    written = []
    for c in wanted:
        sid, text = suggestion_id(rec.incident_id, c.key), question_for(c)
        if sid not in by_id or by_id[sid].question != text:  # same wording: keep its status (asked / dismissed)
            by_id[sid] = CopilotSuggestion(id=sid, incident_id=rec.incident_id, field=c.key, question=text,
                                           reason="contradiction")
        written.append(by_id[sid])
    ws.write(QUESTIONS_FILE, json.dumps([q.model_dump() for q in by_id.values()], indent=2))
    return written


# ---------- questions the incident mapper raises: a place the plan cannot pin to one room ----------
#
# resolve_place.py returns "ambiguous" with a question built from the plan's relations ("over the
# kitchen or over the living room?"). The overlay keeps that report at floor level and the dispatcher
# gets the question, worded by the resolver, never by a model.

PLACE_FIELD = "overlay.place"


def place_question_text(about: str, place: str, question: str) -> str:
    return f"{about.strip().rstrip('.')}: which {place}? {question[:1].upper()}{question[1:]}"


def write_place_question(ws: "Workspace", incident_id: str, about: str, place: str, question: str,
                         room_ids: Sequence[str], evidence_ids: Sequence[str]) -> CopilotSuggestion:
    """Add the dispatcher question for an ambiguous place. One per (about, candidate rooms); an existing one
    keeps its status (asked / dismissed)."""
    key = f"{PLACE_FIELD}.{about}.{'+'.join(sorted(room_ids))}"
    sid = f"{incident_id}--place_{hashlib.sha1(key.encode()).hexdigest()[:10]}"
    by_id = {q.id: q for q in read_questions(ws)}
    if sid not in by_id:
        by_id[sid] = CopilotSuggestion(id=sid, incident_id=incident_id, field=PLACE_FIELD, reason="ambiguous_place",
                                       question=place_question_text(about, place, question),
                                       room_ids=sorted(room_ids), evidence_ids=list(evidence_ids))
        ws.write(QUESTIONS_FILE, json.dumps([q.model_dump() for q in by_id.values()], indent=2))
    return by_id[sid]
