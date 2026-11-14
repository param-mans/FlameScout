import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flamescout_schemas import CallSegment, IncidentOverlay
from services.agents import overlay
from services.agents.overlay import OverlayDraft, assemble, check, diff, fallback, validate_overlay
from services.agents.workspace import FileWorkspace
from tests.test_validate_overlay import CASES as BRUNO_CASES

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
HERE = Path(__file__).resolve().parents[1] / "packages" / "schemas" / "overlay"
HOUSE = "fake_cedar_house"
RESOLVED = {"r4", "r5"}  # what resolve_place resolves on the cedar plan for S5: the kitchen, the garage


@pytest.fixture
def spans(tmp_path):
    """S5's two calls in a workspace transcript."""
    ws = FileWorkspace(tmp_path / "ws")
    segments = [CallSegment(**s) for s in json.loads((CALLS / "s5.json").read_text())["segments"]]
    overlay.append_transcript(ws, segments)
    return overlay.transcript(ws)


def occupant(oid, span, floor=2, status="reported", group=None, count=2, note=None, **loc):
    location = loc or {"kind": "floor", "floor_reported": floor}
    return {"id": oid, "count": count, "location": location, "status": status, "confidence": 0.5,
            "evidence_ids": [span], **({"group_id": group} if group else {}), **({"note": note} if note else {})}


V1 = {  # what the mapper should make of call 1 on Bruno's plan
    "occupants": [occupant("occ-1", "c1:42.0-51.5", note="Call 1: two kids in the back bedroom upstairs")],
    "fire": [{"id": "fire-1", "kind": "smoke", "location": {"kind": "room", "room_id": "r4"},
              "status": "reported", "confidence": 0.8, "evidence_ids": ["c1:12.0-20.5"]}],
    "objectives": [{"id": "obj-1", "type": "rescue", "priority": 1, "target": {"kind": "occupant", "ref_id": "occ-1"},
                    "rationale": "Two children reported upstairs", "evidence_ids": ["c1:42.0-51.5"]}],
    "considerations": [{"id": "con-1", "text": "Consider asking which upstairs bedroom the children are in",
                        "rationale": "The plan has two upstairs bedrooms and the caller said the back one",
                        "confidence": 0.7, "evidence_ids": ["c1:42.0-51.5"], "refs": ["occ-1"]}],
}
V2 = {  # after call 2: the children are contradicted, more reports
    "occupants": [occupant("occ-1", "c1:42.0-51.5", status="contradicted", group="occ-loc-1"),
                  occupant("occ-1b", "c2:12.0-19.0", status="contradicted", group="occ-loc-1"),
                  occupant("occ-2", "c2:30.0-36.0", count=1)],
    "fire": [V1["fire"][0],
             {"id": "fire-2", "kind": "fire", "status": "reported", "confidence": 0.7, "evidence_ids": ["c2:3.0-9.0"],
              "location": {"kind": "anchor", "anchor": {"room_id": "r4", "position": "near_window", "ref_id": "w7"}}}],
    "hazards": [{"id": "haz-1", "kind": "gas", "location": {"kind": "room", "room_id": "r5"}, "status": "reported",
                 "confidence": 0.3, "evidence_ids": ["c2:44.0-47.0"]}],
    "contradictions": [{"group_id": "occ-loc-1", "key": "occupants.location", "resolved_by_dispatcher": False}],
    "objectives": [{**V1["objectives"][0], "target": {"kind": "occupant_group", "ref_id": "occ-loc-1"},
                    "evidence_ids": ["c1:42.0-51.5", "c2:12.0-19.0"]},
                   {"id": "obj-2", "type": "recon", "priority": 2, "target": {"kind": "room", "ref_id": "r5"},
                    "rationale": "Gas smell reported near the garage", "evidence_ids": ["c2:44.0-47.0"]}],
    "considerations": [{**V1["considerations"][0], "refs": ["occ-loc-1"],
                        "evidence_ids": ["c1:42.0-51.5", "c2:12.0-19.0"]}],
}


def build(draft: dict, spans, index, version=1, previous=None) -> IncidentOverlay:
    return assemble(OverlayDraft.model_validate(draft), "inc_s5", "fixture-fake_cedar_house", version, index, spans,
                    previous=previous)


# ---------- transcript and plan index ----------

def test_transcript_has_one_span_per_segment_and_no_duplicates(tmp_path, spans):
    assert [s.span_id for s in spans][:4] == ["c1:0.0-2.0", "c1:12.0-20.5", "c1:30.0-31.0", "c1:42.0-51.5"]
    assert spans[1].speaker == "caller" and spans[0].speaker == "call_taker"
    ws = FileWorkspace(tmp_path / "ws")
    overlay.append_transcript(ws, [CallSegment(call_id="c1", speaker="caller", text="Smoke", t_start=12.0, t_end=20.5)])
    assert len(overlay.transcript(ws)) == len(spans)  # already there
    evidence = overlay.span_evidence(spans[1:2])["c1:12.0-20.5"]
    assert (evidence.kind, evidence.source, evidence.locator["text"]) == (
        "transcript_span", "call", "There's smoke coming out of the kitchen at 42 Cedar Drive.")


def test_plan_index_is_read_from_data_raw_and_missing_means_no_scan(cedar_plan_index):
    assert overlay.load_plan_index(HOUSE) == cedar_plan_index and overlay.has_scan(cedar_plan_index)
    for building in ("fake_wilshire_tower", None, "../etc"):
        assert overlay.load_plan_index(building) == overlay.EMPTY_INDEX
    noscan = overlay.PLAN_INDEX_ROOT / "noscan" / "derived" / "plan_index.json"
    noscan.parent.mkdir(parents=True)
    noscan.write_text((HERE / "plan_index.noscan.example.json").read_text())
    assert not overlay.has_scan(overlay.load_plan_index("noscan"))
    assert (overlay.floor_id_for(cedar_plan_index, 2), overlay.floor_id_for(cedar_plan_index, 3)) == ("f2", None)


# ---------- assemble ----------

def test_code_fills_version_times_floor_ids_and_caps_confidence(spans, cedar_plan_index):
    ov = build(V1, spans, cedar_plan_index)
    assert (ov.incident_id, ov.model_id, ov.version, ov.routes, ov.changes_since_last) == (
        "inc_s5", "fixture-fake_cedar_house", 1, [], [])
    occ = ov.occupants[0]
    assert (occ.location.floor_reported, occ.location.floor_id) == (2, "f2")  # the scan covers floor 2
    by_span = {s.span_id: s for s in spans}
    assert occ.reported_at == by_span["c1:42.0-51.5"].received_at
    assert ov.fire[0].confidence == 0.6  # a caller's report, capped
    assert check(ov, cedar_plan_index, spans, RESOLVED) == []
    assert validate_overlay.validate(json.loads(overlay.dump(ov)), cedar_plan_index) == []
    assert "null" not in overlay.dump(ov)


def test_floor_beyond_the_scan_keeps_only_the_reported_number(spans, cedar_plan_index):
    ov = build({"occupants": [occupant("occ-1", "c1:42.0-51.5", floor=3)]}, spans, cedar_plan_index)
    assert (ov.occupants[0].location.floor_reported, ov.occupants[0].location.floor_id) == (3, None)


# ---------- checks ----------

def rejected(raw: dict, index: dict, spans) -> list[str]:
    """Everything that stops an overlay: the schema first, then check()."""
    try:
        ov = IncidentOverlay.model_validate(raw)
    except ValidationError as e:
        return [str(e)]
    return check(ov, index, spans, RESOLVED)


SCHEMA_SAYS = {  # where the Pydantic model stops one of Bruno's broken cases before his validator runs
    "coordinates are not allowed": "Extra inputs are not permitted",
    "evidence_ids is required": "evidence_ids is required",
    "confidence must be": "less than or equal to 1",
    "start with 'Consider'": "must start with 'Consider'",
    "produced_by must be 'planner'": "Input should be 'planner'",
    "anchor.position must be one of": "Input should be 'center', 'near_door', 'near_window' or 'near_stairs'",
}


@pytest.mark.parametrize("name, mutate, message", BRUNO_CASES, ids=[c[0] for c in BRUNO_CASES])
def test_brunos_broken_cases_are_rejected(name, mutate, message, spans):
    raw = copy.deepcopy(json.loads((HERE / "incident_overlay.example.json").read_text()))
    mutate(raw)
    errors = " | ".join(rejected(raw, json.loads((HERE / "plan_index.example.json").read_text()), spans))
    assert message in errors or SCHEMA_SAYS.get(message, "\0") in errors, errors


def test_brunos_example_guesses_rooms_resolve_place_does_not_resolve(spans, cedar_plan_index):
    """It is valid to his validator, but it puts "the back bedroom upstairs" in r10: resolve_place cannot tell
    r9 from r10 (there is no orientation), so only a floor location is accepted for those children.
    (His span ids "call-1:..." are S5's "c1:..."; the routes are the planner's, not checked here.)"""
    text = (HERE / "incident_overlay.example.json").read_text().replace('"call-1:', '"c1:').replace('"call-2:', '"c2:')
    raw = {**json.loads(text), "routes": []}
    places = {q["type"]: overlay.resolve(cedar_plan_index, overlay.PlaceQuery(about=q["type"], evidence_ids=[], **q))
              for q in ({"type": "kitchen", "floor_reported": 1}, {"type": "garage", "floor_reported": 1},
                        {"type": "bedroom", "floor_reported": 2})}
    assert {k: (r["status"], r["ids"]) for k, r in places.items()} == {
        "kitchen": ("resolved", ["r4"]), "garage": ("resolved", ["r5"]), "bedroom": ("ambiguous", ["r10", "r9"])}
    resolved = {r["ids"][0] for r in places.values() if r["status"] == "resolved"}
    errors = check(IncidentOverlay.model_validate(raw), cedar_plan_index, spans, resolved)
    assert [e.split(":")[0] for e in errors] == ["occ-1a", "occ-1b"]
    assert "room r10 was not resolved by resolve_place" in errors[0]


def test_resolve_place_on_the_incident_index(cedar_plan_index):
    def ask(**q):
        return overlay.resolve(cedar_plan_index, overlay.PlaceQuery(about="x", evidence_ids=[], **q))

    upstairs = ask(type="bedroom", floor_reported=2)
    assert (upstairs["status"], upstairs["question"]) == ("ambiguous", "over the kitchen or over the living room?")
    assert ask(type="bedroom", floor_reported=2, over="kitchen")["ids"] == ["r10"]
    assert ask(type="bedroom", size="largest")["ids"] == ["r10"]
    assert ask(type="bedroom", floor_reported=3) == {"status": "none", "ids": [], "question": None,
                                                     "reason": "the scan has no floor 3"}
    assert ask(type="bedroom", floor_reported=1)["status"] == "none"
    assert overlay.resolve(overlay.EMPTY_INDEX, overlay.PlaceQuery(about="x", evidence_ids=[], type="kitchen"))[
        "reason"] == "no interior scan on file"
    assert overlay.place_label(overlay.PlaceQuery(about="x", evidence_ids=[], type="bedroom", floor_reported=2)) == \
        "bedroom on floor 2"


def mutated(draft: dict, change) -> dict:
    d = copy.deepcopy(draft)
    change(d)
    return d


OUR_RULES = [
    ("evidence that is not a caller span", lambda d: d["fire"][0].update(evidence_ids=["ev_call_1"]), "are not caller spans"),
    ("call-taker words as evidence", lambda d: d["fire"][0].update(evidence_ids=["c1:30.0-31.0"]), "are not caller spans"),
    ("a caller made it verified", lambda d: d["fire"][0].update(status="verified"), "never 'verified'"),
    ("contradicted without a group", lambda d: d["occupants"][2].update(status="contradicted"), "needs the group_id"),
    ("a group from one call", lambda d: d["occupants"][1].update(evidence_ids=["c1:42.0-51.5"]), "different calls"),
    ("the mapper resolved it", lambda d: d["contradictions"][0].update(resolved_by_dispatcher=True), "only the dispatcher"),
    ("a room resolve_place did not resolve", lambda d: d["occupants"][0].update(
        location={"kind": "room", "room_id": "r10"}), "room r10 was not resolved by resolve_place"),
    ("an anchor in an unresolved room", lambda d: d["fire"][1]["location"]["anchor"].update(room_id="r2", ref_id="w7"),
     "room r2 was not resolved"),
    ("an objective on an unresolved room", lambda d: d["objectives"][1].update(target={"kind": "room", "ref_id": "r1"}),
     "room r1 was not resolved"),
    ("a side of a room", lambda d: d["fire"][1]["location"]["anchor"].update(position="north_side", ref_id=None),
     "Input should be"),
    ("a windowless claim", lambda d: d["considerations"][0].update(text="Consider the bedroom with no window"),
     "window findings"),
    ("an invented room", lambda d: d["hazards"][0]["location"].update(room_id="r99"), "does not exist in the plan"),
    ("an invented floor id", lambda d: d["objectives"][1].update(target={"kind": "floor", "ref_id": "floor-2",
                                                                        "floor_reported": 2}), "not a floor id"),
]


@pytest.mark.parametrize("name, change, message", OUR_RULES, ids=[r[0] for r in OUR_RULES])
def test_our_rules(name, change, message, spans, cedar_plan_index):
    assert check(build(V2, spans, cedar_plan_index, 2), cedar_plan_index, spans, RESOLVED) == []
    try:
        errors = check(build(mutated(V2, change), spans, cedar_plan_index, 2), cedar_plan_index, spans, RESOLVED)
    except ValidationError as e:  # the schema stops it first
        errors = [str(e)]
    assert any(message in e for e in errors), errors


def test_no_scan_allows_only_floor_and_unknown(spans):
    index = dict(overlay.EMPTY_INDEX)
    unknown = mutated(V1, lambda d: d["fire"][0].update(location={"kind": "unknown"}))
    del unknown["objectives"]  # the occupant target is fine; keep it simple
    ov = build(unknown, spans, index)
    assert check(ov, index, spans, set()) == [] and ov.occupants[0].location.floor_id is None
    errors = check(build(V1, spans, index), index, spans, RESOLVED)
    assert any("room_id 'r4' does not exist in the plan" in e for e in errors)


# ---------- changes_since_last ----------

def test_changes_since_last_is_a_diff_by_id(spans, cedar_plan_index):
    v1 = build(V1, spans, cedar_plan_index)
    v2 = build(V2, spans, cedar_plan_index, 2, previous=v1)
    assert v2.changes_since_last == [
        "occ-1 is now contradicted (group occ-loc-1)",
        "New: occupants occ-1b (2) on floor 2",
        "New: occupants occ-2 (1) on floor 2",
        "New: fire fire-2 (fire) near window w7 in r4",
        "New: hazard haz-1 (gas) in r5",
        "New contradiction occ-loc-1: occupants.location",
        "Objective obj-1: rescue occupant occ-1 -> rescue occupant_group occ-loc-1",
        "New objective obj-2: recon room r5 (priority 2)",
    ]
    v3 = build(mutated(V2, lambda d: d["hazards"].clear() or d["objectives"].pop()), spans, cedar_plan_index, 3,
               previous=v2)
    assert v3.changes_since_last == ["No longer reported: hazard haz-1 (gas)", "Objective dropped: obj-2"]


def test_fallback_is_valid_and_says_why(cedar_plan_index):
    ov = fallback("inc_s5", "m", 2, "the incident mapper did not submit one")
    assert ov.changes_since_last == ["Overlay not available: the incident mapper did not submit one"]
    assert validate_overlay.validate(json.loads(overlay.dump(ov)), cedar_plan_index) == []


def test_fake_cedar_house_has_the_synthetic_v2_example_as_its_index(monkeypatch, tmp_path):
    monkeypatch.setattr(overlay, "FIXTURE_PLAN_ROOT", Path(__file__).resolve().parents[1] / "data" / "fixtures")
    index = overlay.load_plan_index(HOUSE)  # nothing in data/raw (tmp): the fixture
    example = json.loads((HERE / "plan_index.example.json").read_text())
    assert index["synthetic"] is True and index["version"] == 2
    assert {k: v for k, v in index.items() if k not in ("synthetic", "note")} == example
    assert overlay.resolve(index, overlay.PlaceQuery(about="x", evidence_ids=[], type="kitchen"))["ids"] == ["r4"]


def test_a_floor_alone_names_no_room():
    def q(**kw):
        return overlay.PlaceQuery(about="x", evidence_ids=[], **kw)

    assert not overlay.names_a_room(q(floor_reported=2))
    assert all(overlay.names_a_room(q(**kw)) for kw in ({"type": "bedroom"}, {"over": "kitchen"}, {"at_stairs": False}))
