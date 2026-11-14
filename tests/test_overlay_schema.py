import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flamescout_schemas import IncidentOverlay
from services.agents.overlay import validate_overlay

HERE = Path(__file__).resolve().parents[1] / "packages" / "schemas" / "overlay"
EXAMPLES = [("incident_overlay.example.json", "plan_index.example.json"),
            ("incident_overlay.noscan.example.json", "plan_index.noscan.example.json")]


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text())


@pytest.mark.parametrize("overlay_file, plan_file", EXAMPLES)
def test_brunos_examples_validate_and_round_trip(overlay_file, plan_file):
    raw = load(overlay_file)
    out = IncidentOverlay.model_validate(raw).model_dump(mode="json", exclude_none=True)
    assert out == raw  # field for field, nothing added or lost
    assert validate_overlay.validate(out, load(plan_file)) == []


BAD = [
    ("raw coordinates", lambda o: o["fire"][1]["location"].update(x=3.2), "Extra inputs are not permitted"),
    ("room without room_id", lambda o: o["fire"][0]["location"].pop("room_id"), "needs room_id"),
    ("floor without a floor", lambda o: o["occupants"][2]["location"].pop("floor_reported"), "needs floor_id or floor_reported"),
    ("unknown with a room", lambda o: o["fire"][0]["location"].update(kind="unknown"), "must not carry a room"),
    ("near_window without ref", lambda o: o["fire"][1]["location"]["anchor"].pop("ref_id"), "needs ref_id"),
    ("no count", lambda o: o["occupants"][2].pop("count_range"), "needs count"),
    ("bad count range", lambda o: o["occupants"][2].update(count_range=[3, 1]), "needs count"),
    ("no evidence", lambda o: o["fire"][0].update(evidence_ids=[]), "evidence_ids is required"),
    ("not advisory", lambda o: o["considerations"][0].update(text="Send a crew upstairs"), "must start with 'Consider'"),
    ("agent route", lambda o: o["routes"][0].update(produced_by="agent"), "Input should be 'planner'"),
    ("one waypoint", lambda o: o["routes"][0].update(waypoints=o["routes"][0]["waypoints"][:1]), "at least 2 items"),
    ("version 0", lambda o: o.update(version=0), "greater than or equal to 1"),
    ("floor target without ref", lambda o: o["objectives"][1]["target"].pop("ref_id"), "needs ref_id"),
]


def test_floor_reported_next_to_a_ref_id_is_dropped():
    """The mapper sometimes adds the floor to a room / occupant target; the ref_id says it all."""
    raw = copy.deepcopy(load("incident_overlay.example.json"))
    raw["objectives"][1]["target"].update(kind="room", ref_id="r10", floor_reported=2)
    target = IncidentOverlay.model_validate(raw).objectives[1].target
    assert (target.kind, target.ref_id, target.floor_reported) == ("room", "r10", None)


@pytest.mark.parametrize("name, mutate, message", BAD, ids=[b[0] for b in BAD])
def test_schema_rejects(name, mutate, message):
    raw = copy.deepcopy(load("incident_overlay.example.json"))
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        IncidentOverlay.model_validate(raw)


def test_exported_schema_has_the_overlay_and_its_event():
    defs = json.loads((HERE.parent / "schema.json").read_text())["$defs"]
    assert {"IncidentOverlay", "OverlayOccupant", "OverlayLocation", "Anchor", "OverlayRoute"} <= set(defs)
    assert defs["IncidentOverlay"]["additionalProperties"] is False
    assert "overlay.updated" in defs["Event"]["properties"]["type"]["enum"]
    assert "refs" in defs["Consideration"]["properties"]
