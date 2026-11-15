"""Fast path (services/agents/fast_path.py): room INFERENCE from activity cues and room CANDIDATES, decided in code
whatever the model says. Offline, synthetic plan index, the model's answer given directly (a fake model)."""
from datetime import datetime, timezone

import pytest

from services.agents import fast_path
from services.agents import overlay as overlay_mod
from services.agents.fast_path import FastOutput
from services.agents.overlay import Span


def room(floor, rtype, rank=1, of=1):
    return {"floor_id": floor, "label": rtype.upper(), "type": rtype, "area_m2": 10.0, "size_rank": rank,
            "size_of": of, "next_to": [], "over": [], "under": [], "at_stairs": False}


INDEX = {"version": 2, "floors": ["f1", "f2"], "openings": {}, "stairs": [], "rooms": {
    "k1": room("f1", "kitchen"), "g1": room("f1", "garage"),
    "ba1": room("f1", "bathroom", 1, 2), "ba2": room("f1", "bathroom", 2, 2), "ba3": room("f2", "bathroom"),
    "be1": room("f2", "bedroom"),
    **{f"cl{i}": room("f1", "closet", i, 5) for i in range(1, 6)}}}
ONE_FLOOR = {"version": 2, "floors": ["f1"], "openings": {}, "stairs": [], "rooms": {"k1": room("f1", "kitchen")}}
T = datetime(2026, 11, 14, tzinfo=timezone.utc)


def span(sid, text, call="c1"):
    return Span(span_id=sid, call_id=call, speaker="caller", text=text, t_start=0, t_end=1, received_at=T)


SPANS = [span("c1:0.0-3.0", "I was cooking eggs and the pan caught fire!"),
         span("c1:3.0-6.0", "Upstairs, I was in the shower when the smoke started."),
         span("c1:6.0-9.0", "The clothes dryer is smoking."),
         span("c1:9.0-12.0", "Smoke in the kitchen, I was cooking."),
         span("c1:12.0-15.0", "My son is in the bathroom downstairs."),
         span("c1:15.0-18.0", "He's hiding in the closet."),
         span("c2:0.0-3.0", "The kids are in the bathroom.", "c2"),
         span("c1:18.0-21.0", "Fire in the bathroom downstairs.")]
COOK, SHOWER, DRYER, KITCHEN, SON, CLOSET, KIDS_C2, FIRE_BATH = (s.span_id for s in SPANS)


def run(answer: dict, index=INDEX, spans=SPANS):
    """_to_draft -> assemble -> check, as prepare does. Returns (overlay dict, check errors, questions)."""
    out = FastOutput.model_validate(answer)
    draft, resolved, questions, groups = fast_path._to_draft(out, index, spans)
    ov = overlay_mod.assemble(draft, "inc_t", "m1", 1, index, spans)
    errors = overlay_mod.check(ov, index, spans, resolved, candidate_groups=groups)
    data = __import__("json").loads(overlay_mod.dump(ov))
    assert overlay_mod.validate_overlay.validate(data, index) == []          # never anything schema-invalid
    return data, errors, questions, out


def fire(span_id, place=None, **kw):
    return {"id": "fire-1", "kind": "fire", "confidence": 0.9, "evidence_ids": [span_id], "note": "caller", "place": place,
            **kw}


def occ(span_id, place=None, oid="occ-1", **kw):
    return {"id": oid, "count": 1, "confidence": 0.8, "evidence_ids": [span_id], "note": "caller", "place": place, **kw}


# ---------- inference ----------

@pytest.mark.parametrize("place", [None, {"type": "kitchen"}, {"type": "living room"}])
def test_cooking_infers_the_only_kitchen_whatever_the_model_said(place):
    ov, errors, _, _ = run({"fire": [fire(COOK, place)]})
    f = ov["fire"][0]
    assert errors == []
    assert f["location"] == {"kind": "room", "room_id": "k1"}
    assert f["status"] == "inferred" and f["confidence"] <= 0.6 and f["note"] == "inferred from: cooking"


def test_shower_on_the_floor_said_infers_its_only_bathroom():
    ov, errors, _, _ = run({"fire": [fire(SHOWER, {"floor_reported": 2})]})
    assert errors == [] and ov["fire"][0]["location"] == {"kind": "room", "room_id": "ba3"}
    assert ov["fire"][0]["note"] == "inferred from: shower" and ov["fire"][0]["status"] == "inferred"
    # no floor said: three bathrooms on two floors, nothing to infer, no floor known
    ov, errors, _, _ = run({"fire": [fire(SHOWER)]})
    assert errors == [] and ov["fire"][0]["location"] == {"kind": "unknown"} and ov["fire"][0]["status"] == "reported"


def test_a_cue_whose_room_type_is_not_in_the_plan_gives_floor_level():
    ov, _, _, _ = run({"hazards": [{**fire(DRYER, {"floor_reported": 1}), "id": "haz-1", "kind": "other"}]})
    assert ov["hazards"][0]["location"] == {"kind": "floor", "floor_reported": 1, "floor_id": "f1"}
    assert ov["hazards"][0]["status"] == "reported"
    ov, _, _, _ = run({"fire": [fire(DRYER)]}, index=ONE_FLOOR, spans=SPANS)   # one floor: that floor
    assert ov["fire"][0]["location"] == {"kind": "floor", "floor_reported": 1, "floor_id": "f1"}


def test_a_named_room_is_never_inferred():
    ov, _, _, _ = run({"fire": [fire(KITCHEN, {"type": "kitchen"})]})
    assert ov["fire"][0]["location"] == {"kind": "room", "room_id": "k1"} and ov["fire"][0]["status"] == "reported"
    assert ov["fire"][0]["note"] == "caller"


def test_no_scan_means_no_inference():
    empty = dict(overlay_mod.EMPTY_INDEX)
    ov, errors, _, _ = run({"fire": [fire(COOK)]}, index=empty)
    assert errors == [] and ov["fire"][0]["status"] == "reported" and ov["fire"][0]["location"]["kind"] == "unknown"


# ---------- candidates ----------

def test_an_occupant_in_one_of_two_bathrooms_becomes_two_yellow_candidates():
    answer = {"occupants": [occ(SON, {"type": "bathroom", "floor_reported": 1}, count=1)],
              "objectives": [{"id": "obj-1", "type": "rescue", "priority": 1, "target": {"kind": "occupant", "ref_id": "occ-1"},
                              "rationale": "A child reported in a bathroom downstairs.", "evidence_ids": [SON]}],
              "considerations": [{"category": "life_safety", "title": "Consider searching both downstairs bathrooms",
                                  "rationale": "Caller reported a child in a bathroom downstairs.", "evidence_ids": [SON],
                                  "refs": ["occ-1", "obj-1"]}]}
    ov, errors, questions, out = run(answer)
    assert errors == []
    assert [(o["id"], o["location"]["room_id"], o["count"], o["status"], o["group_id"]) for o in ov["occupants"]] == [
        ("occ-1-1", "ba1", 1, "contradicted", "grp-occ-1"), ("occ-1-2", "ba2", 1, "contradicted", "grp-occ-1")]
    assert ov["contradictions"] == [{"group_id": "grp-occ-1", "key": "occupants.location_candidates",
                                     "resolved_by_dispatcher": False}]
    assert ov["objectives"][0]["target"] == {"kind": "occupant_group", "ref_id": "grp-occ-1"}
    assert out.considerations[0].refs == ["grp-occ-1", "obj-1"]
    assert [sorted(q["result"]["ids"]) for q in questions] == [["ba1", "ba2"]]   # the dispatcher is asked which one


def test_the_one_caller_group_needs_the_exemption():
    out = FastOutput.model_validate({"occupants": [occ(SON, {"type": "bathroom", "floor_reported": 1})]})
    draft, resolved, _, groups = fast_path._to_draft(out, INDEX, SPANS)
    ov = overlay_mod.assemble(draft, "inc_t", "m1", 1, INDEX, SPANS)
    assert groups == {"grp-occ-1"}
    assert any("different calls" in e for e in overlay_mod.check(ov, INDEX, SPANS, resolved))
    assert overlay_mod.check(ov, INDEX, SPANS, resolved, candidate_groups=groups) == []


def test_too_many_matching_rooms_stay_at_floor_level():
    ov, errors, questions, _ = run({"occupants": [occ(CLOSET, {"type": "closet"})]})
    assert errors == [] and len(ov["occupants"]) == 1
    assert ov["occupants"][0]["location"] == {"kind": "floor", "floor_reported": 1, "floor_id": "f1"}
    assert ov["occupants"][0]["status"] == "reported" and len(questions) == 1


def test_fire_matching_two_rooms_is_not_split():
    ov, errors, _, _ = run({"fire": [fire(FIRE_BATH, {"type": "bathroom", "floor_reported": 1})]})
    assert errors == [] and len(ov["fire"]) == 1 and ov["fire"][0]["location"]["kind"] == "floor"


def test_callers_who_disagree_keep_their_own_group():
    answer = {"occupants": [occ(SON, {"type": "bathroom", "floor_reported": 1}, status="contradicted", group_id="g1"),
                            occ(KIDS_C2, {"type": "bedroom"}, oid="occ-2", status="contradicted", group_id="g1")],
              "contradictions": [{"group_id": "g1", "key": "occupants.location"}]}
    ov, errors, _, _ = run(answer)
    assert errors == []
    assert [(o["id"], o["group_id"], o["location"]["kind"]) for o in ov["occupants"]] == [
        ("occ-1", "g1", "floor"), ("occ-2", "g1", "room")]
    assert [c["group_id"] for c in ov["contradictions"]] == ["g1"]


def test_a_floor_only_occupant_is_a_floor_location():
    ov, errors, _, _ = run({"occupants": [occ(SON, {"floor_reported": 2})]})
    assert errors == [] and ov["occupants"][0]["location"] == {"kind": "floor", "floor_reported": 2, "floor_id": "f2"}


def test_nothing_the_checks_accept_is_ever_schema_invalid():
    """Every combination below either passes check() and validate_overlay, or is rejected; run() asserts validity."""
    places = [None, {"type": "bathroom"}, {"type": "bathroom", "floor_reported": 1}, {"type": "kitchen"},
              {"floor_reported": 3}, {"type": "closet"}, {"type": "attic"}]
    for sid in (COOK, SHOWER, DRYER, SON, CLOSET):
        for p in places:
            run({"occupants": [occ(sid, p)], "fire": [fire(sid, p)]})
