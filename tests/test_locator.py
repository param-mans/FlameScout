from flamescout_schemas import CallSegment
from services.agents.fusion import FusionEngine
from services.agents.locator import find_candidates


def incident(text: str):
    eng = FusionEngine("inc_loc")
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text=text, t_start=0, t_end=1))
    return eng.record


def test_matches_by_address_with_abbreviated_suffix():
    [c] = find_candidates(incident("Smoke at 1200 Wilshire Blvd"))
    assert (c.building_id, c.name, c.address, c.score) == (
        "fake_wilshire_tower", "Wilshire Tower", "1200 Wilshire Boulevard", 1.0)


def test_matches_by_building_name():
    [c] = find_candidates(incident("I'm across the street from the Wilshire Tower"))
    assert c.building_id == "fake_wilshire_tower" and c.score == 1.0


def test_no_location_or_no_match_gives_no_candidates():
    assert find_candidates(incident("There's smoke everywhere")) == []
    assert find_candidates(incident("Fire at 77 Elm Street")) == []


def test_unscanned_depot_is_found_and_street_alone_matches_nothing():
    [c] = find_candidates(incident("There's a fire at the warehouse at 48 Quarry Road"))
    assert (c.building_id, c.score) == ("fake_quarry_depot", 1.0)
    assert find_candidates(incident("Smoke at 9 Mill Street")) == []


# ---------- coordinates, scan_available, match_reasons ----------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from flamescout_schemas import BuildingCandidate, LocationCandidate  # noqa: E402
from services.agents.providers import get_providers  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "packages" / "schemas" / "schema.json"


def calls(*texts: str):
    eng = FusionEngine("inc_loc")
    for i, text in enumerate(texts):
        eng.ingest(CallSegment(call_id=f"c{i + 1}", speaker="caller", text=text, t_start=i, t_end=i + 1))
    return eng.record


def test_candidate_says_where_it_is_why_it_matched_and_whether_it_was_scanned():
    [c] = find_candidates(calls("Smoke at 1200 Wilshire Boulevard", "I'm across the street from the Wilshire Tower"))
    assert c.coordinates == {"lat": 34.05, "lon": -118.26}  # from the CoStar property
    assert c.match_reasons == ["address exact match", "name match: Wilshire Tower"]
    assert c.scan_available is True  # the Matterport adapter has a model

    [depot] = find_candidates(calls("There's a fire at the warehouse at 48 Quarry Road"))
    assert (depot.match_reasons, depot.scan_available) == (["address exact match"], False)


def test_partial_matches_name_the_property_and_the_score():
    [c] = find_candidates(calls("I'm standing near the Wilshire Building"))
    assert c.match_reasons == ["name partial match: Wilshire Tower (0.33)"] and c.score == 0.33
    [c] = find_candidates(calls("Smoke at 1200 Wilshire Street"))  # wrong suffix: two of four words
    assert c.match_reasons == ["address partial match: 1200 Wilshire Boulevard (0.50)"] and c.score == 0.5


def test_scan_available_is_none_when_the_matterport_adapter_fails():
    providers = get_providers()

    class Broken:
        def find_model(self, property):
            raise RuntimeError("Matterport down")

    providers.matterport = Broken()
    [c] = find_candidates(calls("Smoke at 1200 Wilshire Boulevard"), providers)
    assert c.scan_available is None and c.match_reasons == ["address exact match"]


def test_same_shape_as_the_rules_pipeline_candidates():
    """The UI renders candidates from both pipelines with one type."""
    assert BuildingCandidate.model_fields.keys() == LocationCandidate.model_fields.keys()
    for name, f in BuildingCandidate.model_fields.items():
        assert f.annotation == LocationCandidate.model_fields[name].annotation, name
    defs = json.loads(SCHEMA.read_text())["$defs"]  # what the frontend types are generated from
    assert defs["BuildingCandidate"]["properties"] == defs["LocationCandidate"]["properties"]

    [c] = find_candidates(calls("Smoke at 1200 Wilshire Boulevard"))
    assert LocationCandidate.model_validate(c.model_dump()).model_dump() == c.model_dump()
    rules_pipeline = LocationCandidate(building_id="wilshire_tower", name="Wilshire Tower", address="1200 Wilshire Blvd",
                                       coordinates={"lat": 34.06, "lon": -118.25}, score=0.92,
                                       match_reasons=["name 'Wilshire Tower' matched (0.92)"], scan_available=True)
    assert BuildingCandidate.model_validate(rules_pipeline.model_dump()).model_dump() == rules_pipeline.model_dump()


def test_candidates_saved_before_this_change_still_load():
    """Tiger's incidents rows keep the candidates of paused incidents across a restart."""
    old = BuildingCandidate(building_id="fake_wilshire_tower", name="Wilshire Tower",
                            address="1200 Wilshire Boulevard", score=1.0)
    assert (old.coordinates, old.match_reasons, old.scan_available) == ({}, [], None)


@pytest.mark.parametrize("text", ["There's smoke everywhere", "Fire at 77 Elm Street"])
def test_nothing_to_explain_when_nothing_matches(text):
    assert find_candidates(calls(text)) == []
