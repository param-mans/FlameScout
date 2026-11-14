import json
from datetime import timedelta
from pathlib import Path

import pytest

from flamescout_schemas import CallSegment, CrewFeedback, now
from services.agents import memory
from services.agents.brief import ConsiderationDraft, build_brief
from services.agents.dossier import load_dossier
from services.agents.fusion import FusionEngine
from services.agents.memory import CrewRecord, feedback
from services.agents.orchestrator import save_incident
from services.agents.workspace import incident_workspace

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
BUILDING = "fake_wilshire_tower"
DRAFT = ConsiderationDraft(category="exposure", title="Consider the building to the east",
                           rationale="A 3-storey building is 4.5 m away; the property is mixed use.",
                           evidence_ids=["ev_aerial_1", "ev_costar_1"])


def briefed_incident(root, incident_id="inc_s1", confirmed=True, with_brief=True):
    """An incident with brief_v1.json in its workspace, built without any agent."""
    eng = FusionEngine(incident_id)
    eng.ingest_all(CallSegment(**s) for s in json.loads((CALLS / "s1.json").read_text())["segments"])
    if confirmed:
        eng.confirm(BUILDING)
    save_incident(eng.record, workspace_root=root)
    if with_brief:
        brief = build_brief(eng.record, load_dossier(BUILDING), [DRAFT])
        incident_workspace(incident_id, root).write("brief_v1.json", brief.model_dump_json())
    return eng


def record(item_id, verdict, kind="marker", label="Stair B (rear / back stairs, north side)", note=None, at=None):
    fb = CrewFeedback(id=f"fb_{item_id}_{verdict}", incident_id="inc_a", building_id=BUILDING, fact_key=item_id,
                      verdict=verdict, note=note, **({"created_at": at} if at else {}))
    return CrewRecord(feedback=fb, kind=kind, label=label)


def test_feedback_is_stored_per_building_and_rendered_for_the_agent(storage):
    briefed_incident(storage)
    fb = feedback("inc_s1", "m_stair_b", "wrong", "bricked up at the alley door", workspace_root=storage)
    assert (fb.building_id, fb.fact_key, fb.verdict, fb.role) == (BUILDING, "m_stair_b", "wrong", "officer")
    fb2 = feedback("inc_s1", "building.stories", "confirmed", workspace_root=storage)
    assert fb2.verdict == "correct"  # the schema's word for confirmed

    [stair, stories] = memory.building_memory(BUILDING)
    assert (stair.kind, stair.name) == ("marker", "Stair B")
    assert (stories.kind, stories.label) == ("fact", "building.stories = 6")
    today = now().date().isoformat()
    notes = memory.memory_workspace().read(f"{BUILDING}/crew_notes.md")
    assert f"- Crew reported: Stair B is wrong: bricked up at the alley door ({today}) " \
           f"[item m_stair_b, evidence {stair.evidence_id}] Do not recommend it." in notes
    assert f"- Crew reported: building.stories = 6 confirmed ({today})" in notes
    assert memory.notes_path(BUILDING) == f"/memories/{BUILDING}/crew_notes.md"
    assert memory.building_memory("some_other_building") == [] and memory.building_memory(None) == []


def test_feedback_is_validated(storage):
    briefed_incident(storage)
    with pytest.raises(ValueError, match="Unknown verdict"):
        feedback("inc_s1", "m_stair_b", "broken", workspace_root=storage)
    with pytest.raises(ValueError, match="not a fact or marker of inc_s1_v1"):
        feedback("inc_s1", "m_no_such_marker", "wrong", workspace_root=storage)
    briefed_incident(storage, "inc_unconfirmed", confirmed=False, with_brief=False)
    with pytest.raises(ValueError, match="no confirmed building"):
        feedback("inc_unconfirmed", "m_stair_b", "wrong", workspace_root=storage)
    briefed_incident(storage, "inc_no_brief", with_brief=False)
    with pytest.raises(ValueError, match="no brief yet"):
        feedback("inc_no_brief", "m_stair_b", "wrong", workspace_root=storage)
    with pytest.raises(ValueError, match="no confirmed building"):
        feedback("inc_never_existed", "m_stair_b", "wrong", workspace_root=storage)
    assert memory.records(BUILDING) == []  # nothing was stored by the failures


def test_newest_verdict_wins_and_an_excluded_marker_can_still_be_confirmed(storage):
    briefed_incident(storage)
    feedback("inc_s1", "m_stair_b", "wrong", "blocked", workspace_root=storage)
    # A later incident's brief no longer has the marker; the crew there can still confirm it.
    eng = briefed_incident(storage, "inc_later", with_brief=False)
    later = build_brief(eng.record, load_dossier(BUILDING), [DRAFT], memory=memory.building_memory(BUILDING))
    assert all(m.id != "m_stair_b" for m in later.markers)
    incident_workspace("inc_later", storage).write("brief_v1.json", later.model_dump_json())
    feedback("inc_later", "m_stair_b", "confirmed", "reopened", workspace_root=storage)

    assert len(memory.records(BUILDING)) == 2
    [current] = memory.building_memory(BUILDING)
    assert current.feedback.verdict == "correct" and current.sentence.startswith("Crew reported: Stair B confirmed: reopened (")


def test_latest_orders_by_time_not_by_position():
    old = record("m_stair_b", "wrong", at=now() - timedelta(days=30))
    new = record("m_stair_b", "correct", at=now())
    assert memory.latest([new, old]) == [new]
