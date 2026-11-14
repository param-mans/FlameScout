"""The data/raw adapters (stored real CoStar / Matterport / Vexcel responses).

Offline tests build small records with the real files' field names in a tmp data/raw. The
`real_data` tests read the sponsor data itself and are skipped where it is absent (CI, fresh clones).
"""
import json
import re
from pathlib import Path

import pytest

from flamescout_schemas import CallSegment
from services.agents import providers
from services.agents.brief import build_brief, is_cross_layer
from services.agents.dossier import load_dossier, pull_layer
from services.agents.fusion import FusionEngine
from services.agents.locator import find_candidates
from services.agents.providers import get_providers, sprinkler_fact

HOUSE, OFFICE = "h1", "c1"
REAL_RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def raw():
    root = providers.RAW_ROOT  # a tmp folder (conftest)
    write(root / HOUSE / "costar" / "listing.json", {
        "id": 1, "listDate": "2026-01-23T00:00:00+00:00",
        "remarks": "Big lawn with a sprinkler system.",
        "resoValues": {"structure": {"stories": 2, "garageSpaces": 2}},
        "property": {"address": {"fullStreetAddress": "18107 Test Oak Lane", "latitude": 29.81, "longitude": -95.68},
                     "mlsPropertyType": "Residential Lease, Detached", "bedsMin": 4, "bathsFull": 2,
                     "bathsHalf": 1, "sqftMin": 3000, "yearBuilt": 2009}})
    write(root / HOUSE / "costar" / "listing_features.json", {"features": [
        {"name": "Sprinkler System", "subcategory1": "Lot Details"},
        {"name": "Fire and Smoke Detector", "subcategory1": "Home Security"}]})
    write(root / HOUSE / "matterport" / "plan.json", {
        "model_id": "mTest", "units": "meters",
        "evidence": {"ev_plan": {"id": "ev_plan", "kind": "scan_floorplan", "source": "matterport"},
                     "ev_evac": {"id": "ev_evac", "kind": "rule_output", "source": "rules"},
                     "ev_stairs": {"id": "ev_stairs", "kind": "rule_output", "source": "rules"},
                     "ev_listing": {"id": "ev_listing", "kind": "costar_record", "source": "costar"}},
        "facts": [{"key": "building.floors_scanned", "value": 2, "status": "verified", "confidence": 0.9,
                   "evidence_ids": ["ev_plan"]},
                  {"key": "building.bedrooms_plan", "value": 5, "status": "inferred", "confidence": 0.7,
                   "evidence_ids": ["ev_plan"]},
                  {"key": "building.bathrooms_plan", "value": 4, "status": "inferred", "confidence": 0.7,
                   "evidence_ids": ["ev_plan"]}],
        "rooms": [{"id": "r1", "label": None, "type": "Garage"}, {"id": "r2", "label": None, "type": "Other"}],
        "exits": [{"door_id": "d1", "floor": 1, "from_room": "r1", "status": "inferred", "confidence": 0.7,
                   "evidence_ids": ["ev_plan", "ev_evac"]},
                  {"door_id": "d2", "floor": 1, "from_room": "r2", "status": "inferred", "confidence": 0.7,
                   "evidence_ids": ["ev_plan", "ev_evac"]}],
        "stairs": [{"id": "stairs-s1", "lower_floor": 1, "upper_floor": 2, "state": "ok", "status": "inferred",
                    "confidence": 0.75, "evidence_ids": ["ev_plan", "ev_stairs"]}],
        "coverage_ratio": {"value": 1, "evidence_ids": ["ev_plan", "ev_listing"]},
        "quality": {"etiquetas": "dormitorio_sin_ventana", "dormitorios_con_ventana": "1"}})
    write(root / HOUSE / "derived" / "bedroom_window_check.json", {"bedroom": "r9", "window": False})
    write(root / OFFICE / "costar" / "property.json", {
        "id": 2, "buildingName": "Sample Trade Center",
        "primaryAddress": {"streetNumber": "9700", "streetName": "Sample", "streetType": "Rd",
                           "latitude": 29.85, "longitude": -95.53},
        "numStoriesAboveGrade": 1, "grossBuildingAreaSquareFeet": 69500, "hasSprinklers": True, "sprinklerType": 1,
        "constructionEvents": [{"eventType": 2, "year": 1979}, {"eventType": 4, "year": 2007}],
        "notes": [{"noteType": 2, "value": "A multi-tenant office/warehouse property. More text."}],
        "propertyFlex": {"driveInBays": {"numDriveInBays": 5, "numFeetWide": 10, "numFeetHigh": 10},
                         "numExternalLoadingDocks": 3, "structural": {"ceilingHeightFeet": 18}}})
    write(root / OFFICE / "costar" / "tenant_companies.json", [
        {"id": 7, "name": "Test Machining ", "numEmployees": 54,
         "profile": {"locationBio": "Precision CNC machining of metal parts. Founded 2005."}}])
    for bid, lat, lon in ((HOUSE, 29.81, -95.68), (OFFICE, 29.85, -95.53)):
        write(root / bid / "vexcel" / "metadata.json", {
            "location": {"lat": lat, "lon": lon},
            "ortho": {"layer": "urban", "collection": "us-tx-test-2026", "captureDate": "2026-01-02T18:43:55",
                      "bbox_meters": 132.8},
            "oblique": {"north": {"productType": "oblique-north", "layer": "urban", "collection": "us-tx-test-2026",
                                  "captureDate": "2026-01-02T18:43:55", "bbox_meters": 90}}})
    return root


# ---------- CoStar ----------

def test_residential_listing_is_mapped_to_our_facts(raw):
    p = get_providers().costar.get_property(HOUSE)
    assert (p["name"], p["address"], p["coordinates"]) == ("18107 Test Oak Lane", "18107 Test Oak Lane",
                                                           {"lat": 29.81, "lon": -95.68})
    facts = {k: f["value"] for k, f in p["facts"].items()}
    assert {k: facts[k] for k in ("building.bedrooms", "building.bathrooms", "building.stories",
                                  "building.area_sqft", "building.year_built")} == {
        "building.bedrooms": 4, "building.bathrooms": 3, "building.stories": 2,  # 2 full + 1 half
        "building.area_sqft": 3000, "building.year_built": 2009}
    assert p["facts"]["building.smoke_detectors"]["evidence_ids"] == ["ev_cs_features"]
    assert {e["kind"] for e in p["evidence"].values()} == {"costar_record"}


@pytest.mark.parametrize("security, lot, claim", [
    (["Fire Sprinkler System"], [], True),
    ([" fire sprinkler system "], [], True),
    (["Sprinkler System"], [], None),                          # the irrigation name, even under Home Security
    (["Fire Sprinklers Partial"], [], None),                   # anything but the exact name: no claim
    (["Security System Owned"], ["Sprinkler System"], None),   # lot irrigation: no claim
    ([], ["remarks"], None),                                   # a sprinkler system mentioned in the remarks
    ([], [], None),
])
def test_sprinkler_rule(security, lot, claim):
    fact = sprinkler_fact(security, lot, "ev_cs_features")
    if claim:
        assert (fact["value"], fact["status"]) == (True, "verified")
    else:
        assert fact["status"] == "unverified" and fact["evidence_ids"] == [] and fact["value"] is not True
        assert ("irrigation" in fact["value"]) == bool(lot)


def test_lot_sprinklers_are_not_fire_sprinklers(raw):
    fact = get_providers().costar.get_property(HOUSE)["facts"]["building.sprinklered"]
    assert fact["status"] == "unverified" and "lawn irrigation" in fact["value"]


def test_commercial_record_is_mapped_to_our_facts(raw):
    p = get_providers().costar.get_property(OFFICE)
    facts = {k: f["value"] for k, f in p["facts"].items()}
    assert (p["name"], p["address"]) == ("Sample Trade Center", "9700 Sample Rd")
    assert facts["building.sprinklered"] is True and p["facts"]["building.sprinklered"]["status"] == "verified"
    assert (facts["building.year_built"], facts["building.year_renovated"]) == (1979, 2007)
    assert facts["building.drive_in_bays"] == "5, 10 x 10 ft" and facts["building.loading_docks"] == 3
    assert facts["building.description"] == "A multi-tenant office/warehouse property."
    assert [t["value"] for t in p["tenants"]] == ["Test Machining (54 employees): Precision CNC machining of metal parts."]
    assert "sprinkler_type" not in json.dumps(p)  # an enum code we cannot decode here: not reported


# ---------- Matterport and Vexcel ----------

def test_plan_gives_exits_stairs_and_facts_and_nothing_about_windows(raw):
    layer = pull_layer("matterport", HOUSE)
    assert layer.scan_available and layer.matterport_model_id == "mTest" and layer.scan_captured_at is None
    labels = {m.id: (m.kind, m.label, m.evidence_ids) for m in layer.markers}
    assert labels == {
        "m_exit_d1": ("exit", "Exterior door from the garage, floor 1", ["ev_mp_plan", "ev_mp_evac"]),
        "m_exit_d2": ("exit", "Exterior door, floor 1", ["ev_mp_plan", "ev_mp_evac"]),
        "stairs-s1": ("stair", "Stairs between floor 1 and floor 2", ["ev_mp_plan", "ev_mp_stairs"])}
    assert layer.facts["building.bedrooms_plan"].value == 5
    assert set(layer.evidence) == {"ev_mp_plan", "ev_mp_evac", "ev_mp_stairs"}  # not the plan's copy of the listing
    dumped = json.dumps(load_dossier(HOUSE).model_dump(mode="json")).lower()
    assert "window" not in dumped and "dormitorio" not in dumped and "quality" not in dumped


def test_vexcel_coverage_is_evidence_only(raw):
    layer = pull_layer("vexcel", HOUSE)
    assert layer.markers == [] and layer.exposures == []
    assert {e: (v.kind, v.source, v.captured_at.isoformat()) for e, v in layer.evidence.items()} == {
        "ev_vx_ortho": ("aerial_crop", "vexcel", "2026-01-02T18:43:55"),
        "ev_vx_oblique_north": ("aerial_crop", "vexcel", "2026-01-02T18:43:55")}


def test_building_without_a_stored_scan_has_no_scan(raw):
    d = load_dossier(OFFICE)
    assert not d.scan_available and d.contradictions == [] and "No interior scan on file" in build_brief(
        FusionEngine("inc").record, d, []).gaps


def test_fixture_buildings_still_come_from_the_fixtures(raw):
    p = get_providers()
    assert p.costar.get_property("fake_wilshire_tower")["name"] == "Wilshire Tower"
    assert p.matterport.find_model({"property_id": "fake_wilshire_tower"})["model_id"] == "fixture-fake_wilshire_tower"
    assert [r["property_id"] for r in p.costar.search_property("Test Oak Lane")] == [HOUSE]


# ---------- listing vs plan ----------

def test_listing_and_scan_disagreeing_is_a_cross_layer_contradiction(raw):
    d = load_dossier(HOUSE)
    assert [c.message for c in d.contradictions] == [
        "Listing: 4 bedrooms; scan: 5. Verify occupant count.",
        "Listing: 3 bathrooms; scan: 4. Verify the layout on arrival."]  # floors: 2 and 2 agree
    bedrooms = d.contradictions[0]
    assert [(v.layer, v.value) for v in bedrooms.values] == [("costar", 4), ("matterport", 5)]
    evidence = [e for v in bedrooms.values for e in v.evidence_ids]
    assert is_cross_layer(evidence, d.evidence)

    record = FusionEngine("inc_h1").record
    record.confirmed_building_id = HOUSE
    brief = build_brief(record, d, [])
    fact = brief.size_up["building.bedrooms"]
    assert (fact.status, fact.evidence_ids) == ("contradicted", ["ev_cs_listing", "ev_mp_plan"])
    assert brief.gaps[:2] == [c.message for c in d.contradictions]
    assert brief.data_contradictions == d.contradictions
    assert all(e in brief.evidence for f in brief.size_up.values() for e in f.evidence_ids)


def test_no_contradiction_when_one_side_is_missing(raw):
    listing = json.loads((raw / HOUSE / "costar" / "listing.json").read_text())
    del listing["resoValues"]["structure"]["stories"]
    listing["property"]["bedsMin"] = 5
    write(raw / HOUSE / "costar" / "listing.json", listing)
    assert [c.key for c in load_dossier(HOUSE).contradictions] == ["building.bathrooms"]


@pytest.mark.parametrize("stories, floors, contradicted", [
    (1.5, 2, False),   # a half storey under the roof is scanned as a second floor
    (1.5, 1, True),
    (1.5, 3, True),
    (2, 2, False),
    (3, 2, True),
])
def test_stories_vs_floors_scanned(raw, stories, floors, contradicted):
    listing = json.loads((raw / HOUSE / "costar" / "listing.json").read_text())
    listing["resoValues"]["structure"]["stories"] = stories
    write(raw / HOUSE / "costar" / "listing.json", listing)
    plan = json.loads((raw / HOUSE / "matterport" / "plan.json").read_text())
    next(f for f in plan["facts"] if f["key"] == "building.floors_scanned")["value"] = floors
    write(raw / HOUSE / "matterport" / "plan.json", plan)
    found = [c for c in load_dossier(HOUSE).contradictions if c.key == "building.stories"]
    assert bool(found) is contradicted
    if found:
        assert found[0].message == f"Listing: {stories:g} floors; scan: {floors}. Verify the number of floors."


def test_lane_and_court_addresses_are_found(raw):
    eng = FusionEngine("inc_x")
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text="Smoke at 18107 Test Oak Ln", t_start=0, t_end=1))
    [c] = find_candidates(eng.record)
    assert (c.building_id, c.match_reasons, c.scan_available) == (HOUSE, ["address exact match"], True)


# ---------- the real sponsor data, where it is present ----------



@pytest.mark.parametrize("bid, contradictions, scan", [
    ("209217448", [], True),
    ("214905148", ["Listing: 4 bedrooms; scan: 5. Verify occupant count.",
                   "Listing: 3 bathrooms; scan: 4. Verify the layout on arrival."], True),
    ("199958956", ["Listing: 4 bedrooms; scan: 3. Verify occupant count."], True),
    ("23296", [], False),
])
@pytest.mark.real_data
@pytest.mark.skipif(not (REAL_RAW / "23296").is_dir(), reason="data/raw (sponsor data) not on this machine")
def test_real_heroes_match_their_readmes(bid, contradictions, scan):
    d = load_dossier(bid)
    assert [c.message for c in d.contradictions] == contradictions and d.scan_available is scan
    dumped = json.dumps(d.model_dump(mode="json")).lower()
    assert "dormitorio" not in dumped and "window" not in dumped


@pytest.mark.real_data
@pytest.mark.skipif(not (REAL_RAW / "23296").is_dir(), reason="data/raw (sponsor data) not on this machine")
def test_real_sprinkler_rule():
    playa = load_dossier("199958956").facts["building.sprinklered"]   # "Sprinkler System" under Lot Details
    assert playa.status == "unverified" and "irrigation" in playa.value
    assert load_dossier("23296").facts["building.sprinklered"].value is True   # hasSprinklers in the record


def test_coverage_metadata_cannot_make_a_consideration_cross_layer(raw):
    """Vexcel here has capture dates only (no analysed image): citing it next to a CoStar fact is one layer."""
    from services.agents.brief import ConsiderationDraft, check_considerations, merge_evidence, observed_evidence

    d = load_dossier(OFFICE)
    record = FusionEngine("inc_c1").record
    record.confirmed_building_id = OFFICE
    roof = ConsiderationDraft(category="roof", title="Consider the roof", rationale="One storey, 18 ft ceilings.",
                              evidence_ids=["ev_cs_property", "ev_vx_ortho"])
    brief = build_brief(record, d, [roof])
    assert brief.considerations[0].cross_layer is False
    assert "ev_vx_ortho" not in observed_evidence(d)
    # only one layer with observations: the cross-layer rule cannot apply, so nothing is rejected for it
    assert check_considerations([roof], merge_evidence(record, d), observed=observed_evidence(d)) == []


@pytest.mark.real_data
@pytest.mark.skipif(not (REAL_RAW / "209217448" / "derived" / "plan_index.json").is_file(),
                    reason="data/raw (sponsor data) not on this machine")
def test_s6_fairdale_places_resolve_on_the_real_plan_index(monkeypatch):
    from services.agents import overlay

    monkeypatch.setattr(overlay, "PLAN_INDEX_ROOT", REAL_RAW)
    index = overlay.load_plan_index("209217448")
    if index.get("version") != 2:
        pytest.skip("the local Fairdale plan index is v1 (no relations); regenerate it")
    script = json.loads((Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls" / "s6.json").read_text())
    ids = set(re.findall(r"\b(?=\w*\d)[a-z0-9]{11,25}\b", script["description"])) - {"209217448"}
    assert ids and ids <= set(index["rooms"]) | set(index["floors"])  # the expected ids are real plan ids

    def ask(**q):
        return overlay.resolve(index, overlay.PlaceQuery(about="x", evidence_ids=[], **q))

    assert ask(type="kitchen", floor_reported=1)["ids"] == ["j7nhq2abkp0"]
    assert ask(type="office")["ids"] == ["rewssvxhpxq"]
    assert ask(type="garage", floor_reported=1)["ids"] == ["tfsfsskzu72"]
    downstairs = ask(type="bedroom", floor_reported=1)
    assert (downstairs["status"], downstairs["ids"]) == (
        "ambiguous", ["2qdmc5i9byxi79ry1pxdkqzea", "d3slsxy3zlg", "tt6gm2a0463x9wmnx7rn6gugc"])
    assert ask(type="bedroom", floor_reported=2)["status"] == "none"  # upstairs has no bedroom

    eng = FusionEngine("inc_s6")
    eng.ingest(CallSegment(**script["segments"][1]))
    assert find_candidates(eng.record)[0].building_id == "209217448"
