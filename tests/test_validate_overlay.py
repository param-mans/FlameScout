"""Tests for the IncidentOverlay validator. Runs with pytest or plain `python3 tests/test_validate_overlay.py`.
The validator and the example files live in packages/schemas/overlay/. If you move them, change HERE."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "packages" / "schemas" / "overlay"
sys.path.insert(0, str(HERE))

import validate_overlay as v  # noqa: E402


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


OVERLAY = _load("incident_overlay.example.json")
PLAN = _load("plan_index.example.json")
NOSCAN_OVERLAY = _load("incident_overlay.noscan.example.json")
NOSCAN_PLAN = _load("plan_index.noscan.example.json")


def _errors(mutate, overlay: dict = OVERLAY, plan: dict = PLAN) -> list[str]:
    o = copy.deepcopy(overlay)
    mutate(o)
    return v.validate(o, plan)


def test_example_is_valid() -> None:
    assert v.validate(OVERLAY, PLAN) == []


def test_noscan_example_is_valid() -> None:
    assert v.validate(NOSCAN_OVERLAY, NOSCAN_PLAN) == []


def test_noscan_rejects_room_ids() -> None:
    errs = _errors(lambda o: o["fire"][0].update(location={"kind": "room", "room_id": "r1"}), NOSCAN_OVERLAY, NOSCAN_PLAN)
    assert any("does not exist in the plan" in e for e in errs)


CASES = [
    ("invented room id", lambda o: o["occupants"][0]["location"].update(room_id="r99"), "does not exist in the plan"),
    ("coordinates in output", lambda o: o["fire"][1]["location"].update(x=3.2), "coordinates are not allowed"),
    ("no evidence", lambda o: o["fire"][0].update(evidence_ids=[]), "evidence_ids is required"),
    ("confidence out of range", lambda o: o["fire"][0].update(confidence=1.4), "confidence must be"),
    ("directive wording", lambda o: o["considerations"][0].update(text="Consider this but you must go now"), "directive wording"),
    ("not advisory", lambda o: o["considerations"][0].update(text="Send a crew upstairs"), "start with 'Consider'"),
    ("group without contradiction entry", lambda o: o.update(contradictions=[]), "has no entry in contradictions"),
    ("group member not contradicted", lambda o: o["occupants"][0].update(status="reported"), "must have status 'contradicted'"),
    ("route made by the agent", lambda o: o["routes"][0].update(produced_by="agent"), "produced_by must be 'planner'"),
    ("waypoint does not exist", lambda o: o["routes"][0]["waypoints"][3].update(ref_id="s9"), "does not exist"),
    ("anchor ref does not exist", lambda o: o["fire"][1]["location"]["anchor"].update(ref_id="w99"), "anchor.ref_id"),
    ("objective target does not resolve", lambda o: o["objectives"][0]["target"].update(ref_id="nope"), "does not resolve"),
    ("duplicate id", lambda o: o["hazards"][0].update(id="fire-1"), "duplicate id"),
]


def test_negative_cases() -> None:
    for name, mutate, expect in CASES:
        errs = _errors(mutate)
        assert any(expect in e for e in errs), "%s: expected %r in %r" % (name, expect, errs)


if __name__ == "__main__":
    for fn in (test_example_is_valid, test_noscan_example_is_valid, test_noscan_rejects_room_ids, test_negative_cases):
        fn()
        print("OK", fn.__name__)
