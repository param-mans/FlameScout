import json
from pathlib import Path

from flamescout_schemas import CallSegment, Contradiction, ContradictionValue
from services.agents import copilot
from services.agents.fusion import FusionEngine
from services.agents.workspace import FileWorkspace

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"


def s2_engine() -> FusionEngine:
    eng = FusionEngine("inc_s2")
    eng.ingest_all(CallSegment(**s) for s in json.loads((CALLS / "s2.json").read_text())["segments"])
    return eng


def test_floor_contradiction_becomes_a_question_with_the_callers_values(tmp_path):
    eng, ws = s2_engine(), FileWorkspace(tmp_path)
    [q] = copilot.write_questions(ws, eng.record)
    assert q.question == "Callers disagree on floor (3 vs 4). Ask: which floor are the flames or smoke on?"
    [saved] = copilot.read_questions(ws)
    assert (saved.id, saved.incident_id, saved.field, saved.reason, saved.status) == (
        copilot.suggestion_id("inc_s2", "incident.floor"), "inc_s2", "incident.floor", "contradiction", "open")
    assert not ws.exists("copilot.json")  # that file is the API's status map; this never touches it


def test_trapped_contradiction_and_only_life_safety_keys():
    record = FusionEngine("inc_t").record
    span = {"start": 0, "end": 1, "text": "x"}
    record.contradictions = [
        Contradiction(key="people.trapped", values=[ContradictionValue(call_id="c1", value=True, span=span),
                                                    ContradictionValue(call_id="c2", value=False, span=span)]),
        Contradiction(key="alarm.sounding", values=[ContradictionValue(call_id="c1", value=True, span=span),
                                                    ContradictionValue(call_id="c2", value=False, span=span)]),
    ]
    [c] = copilot.unresolved_life_safety(record)
    assert copilot.question_for(c) == ("Callers disagree on whether anyone is trapped (yes vs no). "
                                       "Ask: is anyone trapped or unaccounted for, and where?")


def test_resolved_contradictions_ask_nothing_and_statuses_survive_a_rewrite(tmp_path):
    eng, ws = s2_engine(), FileWorkspace(tmp_path)
    copilot.write_questions(ws, eng.record)
    [saved] = copilot.read_questions(ws)
    saved.status = "asked"  # the dispatcher marked it in the UI
    ws.write(copilot.QUESTIONS_FILE, json.dumps([saved.model_dump()]))
    copilot.write_questions(ws, eng.record)  # same disagreement again
    assert copilot.read_questions(ws)[0].status == "asked"
    assert copilot.write_questions(ws, eng.record, keys=["people.trapped"]) == []

    eng.resolve("incident.floor", 4)
    assert copilot.unresolved_life_safety(eng.record) == []
    other = FileWorkspace(tmp_path / "other")
    assert copilot.write_questions(other, eng.record) == [] and not other.exists(copilot.QUESTIONS_FILE)


def test_api_suggestions_are_unchanged_by_the_orchestrator_questions():
    """services/api still derives its own suggestions from the record (contradictions + missing fields)."""
    out = copilot.suggestions(s2_engine().record, {})
    assert out[0].reason == "contradiction" and out[0].field == "incident.floor"
    assert any(s.reason == "missing" for s in out)
