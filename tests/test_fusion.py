import json
from pathlib import Path

from flamescout_schemas import CallSegment
from services.agents.fusion import FusionEngine

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"


def load(script_id: str) -> list[CallSegment]:
    data = json.loads((CALLS / f"{script_id.lower()}.json").read_text())
    return [CallSegment(**s) for s in data["segments"]]


def test_s1_ready_to_brief():
    rec = FusionEngine("inc_s1").ingest_all(load("S1"))
    assert rec.ready_to_brief
    assert rec.status == "locating"
    assert rec.facts["incident.floor"].value == 5
    assert rec.facts["people.reported"].value is True
    assert rec.facts["alarm.sounding"].value is True
    assert rec.contradictions == []


def test_s2_floor_contradiction_has_both_spans():
    rec = FusionEngine("inc_s2").ingest_all(load("S2"))
    floor = rec.facts["incident.floor"]
    assert floor.status == "contradicted"
    assert len(rec.contradictions) == 1
    c = rec.contradictions[0]
    assert c.key == "incident.floor"
    assert {v.value for v in c.values} == {3, 4}
    assert {v.call_id for v in c.values} == {"c1", "c2"}
    assert all(v.span.get("text") for v in c.values)
    assert not c.resolved_by_dispatcher


def test_s2_merges_non_conflicting_facts():
    rec = FusionEngine("inc_s2").ingest_all(load("S2"))
    assert "back stairs" in rec.facts["access.notes"].value
    assert rec.facts["people.reported"].value is True
    assert rec.facts["location.name"].value == "Wilshire Tower"


def test_every_fact_has_evidence_and_capped_confidence():
    rec = FusionEngine("inc_s2").ingest_all(load("S2"))
    for f in rec.facts.values():
        assert f.evidence_ids, f.key
        assert all(e in rec.evidence for e in f.evidence_ids)
        assert f.confidence <= 0.6
        assert f.status in ("reported", "contradicted")


def test_open_questions_shrink_as_facts_arrive():
    eng = FusionEngine("inc_q")
    assert any("trapped" in q for q in eng.record.open_questions)
    eng.ingest(CallSegment(call_id="c1", speaker="caller",
                           text="Someone is trapped on the third floor", t_start=0, t_end=2))
    assert not any("trapped" in q for q in eng.record.open_questions)


def test_same_caller_correction_is_not_a_contradiction():
    eng = FusionEngine("inc_fix")
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text="It's the third floor", t_start=0, t_end=1))
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text="Sorry, the fourth floor", t_start=2, t_end=3))
    assert eng.record.facts["incident.floor"].value == 4
    assert eng.record.contradictions == []


def test_dispatcher_resolves_contradiction():
    eng = FusionEngine("inc_s2")
    eng.ingest_all(load("S2"))
    eng.resolve("incident.floor", 4)
    assert eng.record.facts["incident.floor"].status == "reported"
    assert eng.record.contradictions[0].resolved_by_dispatcher


def test_call_taker_speech_is_ignored():
    eng = FusionEngine("inc_ct")
    eng.ingest(CallSegment(call_id="c1", speaker="call_taker",
                           text="Is it the third floor?", t_start=0, t_end=1))
    assert eng.record.facts == {}


def test_confirm_sets_building_and_status():
    eng = FusionEngine("inc_c")
    eng.ingest_all(load("S1"))
    eng.confirm("fake_wilshire_tower")
    assert (eng.record.confirmed_building_id, eng.record.status) == ("fake_wilshire_tower", "confirmed")
