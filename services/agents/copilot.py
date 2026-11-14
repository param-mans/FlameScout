"""L1 dispatcher copilot (PRD FR-11): suggest the next question from gaps. Plain function.

Suggestions appear for missing high-value fields and unresolved contradictions,
and disappear as soon as the field is filled / the contradiction resolved.
Dismissed / asked state is kept by the caller (service) and passed in.
"""
from __future__ import annotations

from flamescout_schemas import CopilotSuggestion, IncidentRecord

from .fusion import QUESTIONS


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
