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
