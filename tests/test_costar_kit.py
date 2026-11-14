import json

from flamescout_schemas import Fact
from services.agents import brief as B
from services.agents.dossier import build_dossier
from services.agents.tools import rules
from services.agents.tools.costar_kit import Enums, floor_label, format_address, normalize_property, use_label
from services.agents.tools.providers import CombinedCoStarProvider, get_providers
from services.agents.tools.rules import load_config

from .conftest import FIXTURES

CFG = load_config()
E = Enums({
    "PropertyType": {"0": "Unknown", "11": "Multifamily", "5": "Office"},
    "PropertySubtype": {"0": "Unknown", "58": "MultiFamily_Apartments", "64": "Office_Medical"},
    "BuildingConstructionMaterialType": {"0": "Unknown", "3": "ReinforcedConcrete", "5": "WoodFrame"},
    "SprinklerType": {"0": "Unknown", "1": "Wet"},
    "ConstructionStatus": {"1": "Existing"},
    "ElevatorType": {"1": "Passenger", "2": "Freight", "3": "Escalator"},
    "FloorName": {"20": "Basement", "150": "Penthouse"},
})

RAW = {
    "id": 123,
    "primaryAddress": {"streetNumber": "100", "streetDirection": "N", "streetName": "Main", "streetType": "St",
                       "city": "Houston", "state": "TX", "postcode": "770021234",
                       "latitude": 29.76, "longitude": -95.36},
    "propertyType": 11, "propertySubtype": 58, "buildingName": "Main Street Lofts",
    "numStoriesAboveGrade": 6, "buildingHeightInFeet": 72.0,
    "constructionEvents": [{"eventType": 2, "year": 1999}, {"eventType": 4, "year": 2015}],
    "grossBuildingAreaSquareFeet": 120000.0, "buildingConstructionMaterial": 5,
    "hasSprinklers": None, "sprinklerType": 1, "constructionStatus": 1,
    "propertyApartment": {"totalNumUnits": 140, "numBuildings": 1, "isWalkup": False,
                          "elevators": [{"elevatorType": 1, "numElevators": 2, "elevatorState": 1},
                                        {"elevatorType": 3, "numElevators": 4, "elevatorState": 1}]},
    "attachments": [{"videoExternalReferenceType": 36, "videoKey": "abcDEF12345"},
                    {"videoExternalReferenceType": 0, "videoKey": None}],
    "primaryPhoto": {"attachmentUri": "https://example.invalid/112/image.jpg"},
}


def test_normalize_property():
    p = normalize_property(RAW, E, "2026-11-09", tenants=[{"name": "Corner Cafe", "category": "Food", "floor": 1}])
    assert p["property_id"] == "123" and p["name"] == "Main Street Lofts"
    assert p["address"] == "100 N Main St, Houston, TX 77002"
    assert p["use"] == "Multifamily - Apartments"
    assert (p["stories"], p["height_ft"], p["year_built"], p["year_renovated"]) == (6, 72.0, 1999, 2015)
    assert p["construction"] == "Wood frame"
    assert p["sprinklered"] is True and p["sprinkler_type"] == "Wet"   # type recorded -> system present
    assert p["units"] == 140 and p["elevators"] == 2 and p["walkup"] is False   # escalators not counted
    assert p["matterport_model_ids"] == ["abcDEF12345"] and p["matterport_model_id"] == "abcDEF12345"
    assert p["tenants_total"] == 1 and p["as_of"] == "2026-11-09"


def test_unknowns_are_dropped_not_guessed():
    raw = {"id": 9, "propertyType": 0, "propertySubtype": 0, "buildingConstructionMaterial": 0,
           "sprinklerType": 0, "hasSprinklers": None, "primaryAddress": None}
    p = normalize_property(raw, E, "2026-11-09")
    for k in ("use", "construction", "sprinklered", "sprinkler_type", "stories", "year_built", "lat"):
        assert k not in p


def test_labels():
    assert use_label(E, 5, 64) == "Office - Medical"
    assert format_address({"streetNumber": "1", "streetNumberEndRange": "9", "streetName": "Elm",
                           "city": "Los Angeles", "state": "CA"}) == "1-9 Elm, Los Angeles, CA"
    assert [floor_label(E, c) for c in (24, 25, 27, 34, 20, 150, 0)] == [1, 2, 3, 10, "Basement", "Penthouse", None]


def _index(tmp_path):
    p = normalize_property(RAW, E, "2026-11-09", tenants=[{"name": "Corner Cafe", "category": "Restaurant", "floor": 1}])
    path = tmp_path / "properties.jsonl"
    path.write_text(json.dumps(p) + "\n")
    return path


def test_kit_provider_search_get_and_fixture_passthrough(tmp_path):
    P = get_providers("fixture", FIXTURES, {"COSTAR_INDEX": str(_index(tmp_path))})
    assert isinstance(P.costar, CombinedCoStarProvider)
    hits = P.costar.search_property("100 North Main Street")
    assert hits[0]["property_id"] == "123" and hits[0]["score"] == 1.0
    assert P.costar.get_property("123")["units"] == 140
    assert P.costar.get_property("wilshire_tower")["name"] == "Wilshire Tower"   # S1-S4 still work
    assert get_providers("live", FIXTURES, {"COSTAR_INDEX": str(_index(tmp_path))}).costar is not None


def test_missing_index_falls_back_to_fixtures(tmp_path):
    P = get_providers("fixture", FIXTURES, {"COSTAR_INDEX": str(tmp_path / "nope.jsonl")})
    assert not isinstance(P.costar, CombinedCoStarProvider)


def test_dossier_and_brief_on_kit_building(tmp_path):
    P = get_providers("fixture", FIXTURES, {"COSTAR_INDEX": str(_index(tmp_path))})
    d = build_dossier("123", P, CFG, tmp_path)
    assert d.facts["building.height_est_m"].value == 21.9          # 72 ft recorded, not 6 x 3.5 m
    assert d.facts["building.height_est_m"].status == "verified"
    assert d.facts["building.units"].value == 140
    assert d.tenants[0].value["floor"] == 1
    assert not d.scan_available and d.matterport_model_id == "abcDEF12345"
    for f in list(d.facts.values()) + d.tenants:
        assert f.evidence_ids and all(e in d.evidence for e in f.evidence_ids)
    hazards = {f.key for f, _ in rules.r04_hazards(d.facts["building.use"], d.tenants, CFG)}
    assert {"hazard.sleeping_occupants", "hazard.cooking"} <= hazards


def test_r01_prefers_recorded_height():
    ev = ["ev_costar_x"]
    stories = Fact(key="building.stories", value=6, status="verified", confidence=0.9, evidence_ids=ev)
    ft = Fact(key="building.height_ft", value=100, status="verified", confidence=0.9, evidence_ids=ev)
    f, e = rules.r01_height(stories, CFG, ft)
    assert f.value == 30.5 and "recorded height" in e.locator["formula"]
    assert rules.r01_height(stories, CFG, None)[0].value == 21.0


def test_brief_lists_kit_keys():
    assert {"building.units", "building.sprinkler_type", "building.elevators"} <= set(B.BUILDING_KEYS)
