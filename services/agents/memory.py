"""Long-term building memory: what crews found on scene, kept per building. No LLM.

An officer marks a fact or marker of a brief as wrong, outdated or confirmed
(feedback()). That is stored under <building_id>/ in a persistent store:

  <building_id>/feedback.json    every record (a CrewFeedback + what the item was)
  <building_id>/crew_notes.md    the same, rendered for the agent: newest verdict per item

in MongoDB (namespace ("memories",)) when MONGODB_URI is set, otherwise under
data/memories/ (not committed). The orchestrator mounts the store read-only at
/memories/, so on a later incident at the same building the strategy subagent
reads /memories/<building_id>/crew_notes.md, and brief.py applies the records in
code: a "Crew reported: ..." fact per item, and markers marked wrong are left out.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from pydantic import BaseModel

from flamescout_schemas import Brief, CrewFeedback, IncidentRecord

if TYPE_CHECKING:
    from .workspace import Workspace

MEMORY_ROOT = Path(__file__).resolve().parents[2] / "data" / "memories"
# What the officer says -> CrewFeedback.verdict (the schema spells "confirmed" as "correct").
VERDICTS = {"wrong": "wrong", "outdated": "outdated", "confirmed": "correct", "correct": "correct"}
_PHRASE = {"wrong": "is wrong", "outdated": "is outdated", "correct": "confirmed"}


class CrewRecord(BaseModel):
    feedback: CrewFeedback
    kind: Literal["fact", "marker"]
    label: str  # the item as the brief showed it when the officer marked it

    @property
    def item_id(self) -> str:
        return self.feedback.fact_key

    @property
    def evidence_id(self) -> str:
        return f"ev_crew_{self.feedback.id}"

    @property
    def name(self) -> str:
        """Short name of the item: a marker's label up to its first bracket or comma ("Stair B")."""
        return re.split(r"\s*[(,;:]", self.label, maxsplit=1)[0].strip() if self.kind == "marker" else self.label

    @property
    def sentence(self) -> str:
        fb = self.feedback
        note = f": {fb.note.strip()}" if fb.note and fb.note.strip() else ""
        return f"Crew reported: {self.name} {_PHRASE[fb.verdict]}{note} ({fb.created_at.date().isoformat()})"


def latest(records: list[CrewRecord]) -> list[CrewRecord]:
    """One record per item: the newest verdict wins (a later "confirmed" undoes an earlier "wrong")."""
    by_item: dict[str, CrewRecord] = {}
    for r in sorted(records, key=lambda r: r.feedback.created_at):
        by_item[r.item_id] = r
    return list(by_item.values())


def render(building_id: str, records: list[CrewRecord]) -> str:
    lines = [f"# Crew memory for {building_id}", "",
             "Reports from crews at earlier incidents in this building. Newest verdict per item.", ""]
    for r in latest(records):
        action = " Do not recommend it." if r.kind == "marker" and r.feedback.verdict == "wrong" else ""
        lines.append(f"- {r.sentence} [item {r.item_id}, evidence {r.evidence_id}]{action}")
    return "\n".join(lines) + "\n"


# ---------- the store ----------

def _check(building_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", building_id or ""):
        raise ValueError(f"Bad building id {building_id!r}")
    return building_id


def memory_workspace() -> "Workspace":
    """Every building's memory. Mounted by the orchestrator at /memories/."""
    from . import mongo_store
    from .workspace import FileWorkspace, StoreWorkspace

    if mongo_store.enabled():
        return StoreWorkspace(mongo_store.default_store(), ("memories",))
    return FileWorkspace(MEMORY_ROOT)


def notes_path(building_id: str) -> str:
    """Where the agent reads this building's memory."""
    return f"/memories/{_check(building_id)}/crew_notes.md"


def records(building_id: str) -> list[CrewRecord]:
    text = memory_workspace().read_or_none(f"{_check(building_id)}/feedback.json")
    return [CrewRecord(**r) for r in json.loads(text)] if text else []


def building_memory(building_id: Optional[str]) -> list[CrewRecord]:
    """The newest record per item for this building. Empty when there is none (or no building yet)."""
    return latest(records(building_id)) if building_id else []


def _latest_brief(ws: "Workspace") -> Optional[Brief]:
    versions = [int(m[1]) for rel in ws.list("brief_v") if (m := re.fullmatch(r"brief_v(\d+)\.json", rel))]
    return Brief.model_validate_json(ws.read(f"brief_v{max(versions)}.json")) if versions else None


def feedback(incident_id: str, item_id: str, verdict: str, note: Optional[str] = None,
             role: str = "officer", workspace_root: Optional[Path] = None) -> CrewFeedback:
    """The officer marks a fact or marker of the incident's latest brief as wrong, outdated or confirmed.

    Stored in the confirmed building's memory, so it applies to the next brief version of this
    incident and to every later incident at that building. Raises ValueError for an unknown
    verdict, an incident without a confirmed building or a brief, or an item that is neither in the
    latest brief nor already in the building's memory.
    """
    from .workspace import incident_workspace

    if verdict not in VERDICTS:
        raise ValueError(f"Unknown verdict {verdict!r}: use wrong, outdated or confirmed")
    ws = incident_workspace(incident_id, workspace_root)
    incident_text = ws.read_or_none("incident.json")
    building_id = IncidentRecord.model_validate_json(incident_text).confirmed_building_id if incident_text else None
    if not building_id:
        raise ValueError(f"Incident {incident_id} has no confirmed building; there is nothing to give feedback on")
    brief = _latest_brief(ws)
    if brief is None:
        raise ValueError(f"Incident {incident_id} has no brief yet")

    markers = {m.id: m.label for m in brief.markers}
    facts = {f.key: f for f in [*brief.size_up.values(), *brief.hazards] if not f.key.startswith("crew.")}
    known = {r.item_id: r for r in records(building_id)}  # e.g. a marker left out since it was marked wrong
    if item_id in markers:
        kind, label = "marker", markers[item_id]
    elif item_id in facts:
        kind, label = "fact", f"{item_id} = {facts[item_id].value}"
    elif item_id in known:
        kind, label = known[item_id].kind, known[item_id].label
    else:
        raise ValueError(f"{item_id!r} is not a fact or marker of {brief.brief_id}")

    fb = CrewFeedback(id=f"fb_{uuid.uuid4().hex[:10]}", incident_id=incident_id, building_id=building_id,
                      fact_key=item_id, verdict=VERDICTS[verdict], note=note, role=role)  # type: ignore[arg-type]
    everything = [*records(building_id), CrewRecord(feedback=fb, kind=kind, label=label)]  # type: ignore[arg-type]
    store = memory_workspace()
    store.write(f"{building_id}/feedback.json", json.dumps([r.model_dump(mode="json") for r in everything], indent=2))
    store.write(f"{building_id}/crew_notes.md", render(building_id, everything))
    return fb
