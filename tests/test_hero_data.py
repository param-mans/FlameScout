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


# ---------- Vexcel images read by Gemini vision (aerial.py) ----------
#
# A synthetic reading of a 100 m ortho (north up), so the geometry is easy to check by hand:
# box_2d is [ymin, xmin, ymax, xmax] on 0-1000, the image centre is the building's point.

def _seen(box, conf=0.8, **kw):
    return {"label": kw.pop("label", "seen"), "box_2d": box, "confidence": conf, **kw}


SYNTHETIC_READING = {
    "building_id": HOUSE, "model": "test-vision", "prompt_version": 1, "read_at": "2026-11-14T20:00:00+00:00",
    "views": {
        "ortho": {"target_found": True, "target": [400, 400, 600, 600], "target_confidence": 0.9, "roof": "pitched",
                  "roof_features": [_seen([420, 420, 450, 480], kind="solar_panels", label="panels on south slope")],
                  "neighbours": [_seen([400, 700, 600, 800], kind="house", label="house"),          # 10 m east
                                 _seen([450, 620, 550, 680], kind="garage_or_shed", label="shed"),  # 2 m east
                                 _seen([0, 0, 50, 50], kind="house", label="far house"),            # 49.5 m: dropped
                                 _seen([400, 100, 600, 200], 0.2, kind="house", label="unsure")],   # low confidence
                  "site": [_seen([600, 450, 1000, 550], kind="driveway", label="driveway to the street"),
                           _seen([700, 100, 800, 200], kind="pool", label="pool")]},
        "oblique-north": {"target_visible": True, "stories": 2, "stories_confidence": 0.8,
                          "entries": [_seen([500, 450, 600, 500], 0.9, kind="front_door", label="front door")]},
        "oblique-east": {"target_visible": True, "stories": 2, "stories_confidence": 0.7, "entries": []},
        "oblique-south": {"target_visible": True, "stories": 3, "stories_confidence": 0.5, "entries": []},
        "oblique-west": {"target_visible": False, "stories": None, "stories_confidence": 0, "entries": []}}}


def with_reading(root: Path, reading: dict = SYNTHETIC_READING) -> None:
    meta = json.loads((root / HOUSE / "vexcel" / "metadata.json").read_text())
    for d in ("east", "south", "west"):
        meta["oblique"][d] = {**meta["oblique"]["north"], "productType": f"oblique-{d}"}
    write(root / HOUSE / "vexcel" / "metadata.json", meta)
    images = {"ortho": {"file": "ortho.jpg", "bbox_meters": 100}}
    images |= {f"oblique-{d}": {"file": f"oblique-{d}.jpg", "bbox_meters": 90} for d in ("north", "east", "south", "west")}
    write(root / HOUSE / "vexcel" / "images.json", {"images": images})
    write(root / HOUSE / "derived" / "aerial_reading.json", reading)


def test_aerial_geometry_is_computed_in_code():
    from services.agents import aerial

    target = aerial.to_metres([400, 400, 600, 600], 100)
    assert target == pytest.approx((-10, -10, 10, 10))
    assert aerial.gap_m(target, aerial.to_metres([400, 700, 600, 800], 100)) == pytest.approx(10)
    assert aerial.gap_m(target, aerial.to_metres([450, 450, 550, 550], 100)) == 0   # overlapping
    assert aerial.side_of(target, aerial.to_metres([0, 450, 100, 550], 100)) == "north"   # top of the image
    assert aerial.side_of(target, aerial.to_metres([800, 800, 900, 900], 100)) == "southeast"
    pos = aerial.lat_lon([0, 450, 100, 550], 100, 29.81, -95.68)   # 45 m north of the centre
    assert pos == {"lat": pytest.approx(29.81 + 45 / 111_320, abs=1e-7), "lon": pytest.approx(-95.68)}


@pytest.mark.parametrize("stories, expected", [
    ({"n": (2, 0.8), "e": (2, 0.7), "s": (3, 0.5)}, {"value": 2, "views": ["e", "n"], "confidence": 0.7}),  # one-off 3
    ({"n": (2, 0.9), "e": (1, 0.9), "s": (2, 0.75), "w": (1, 0.85)},                 # two storeys + a one-storey wing
     {"value": 2, "views": ["n", "s"], "confidence": 0.75}),
    ({"n": (2, 0.8), "e": (3, 0.8)}, None),                    # two views, no agreement
    ({"n": (2, 0.3)}, None),                                   # too unsure to count
    ({"n": (1, 0.9)}, {"value": 1, "views": ["n"], "confidence": 0.54}),   # one view only: reduced
])
def test_stories_the_obliques_agree_on(stories, expected):
    from services.agents import aerial

    views = {v: {"target_visible": True, "stories": s, "stories_confidence": c} for v, (s, c) in stories.items()}
    assert aerial.stories_seen(views) == expected


def test_vexcel_reading_gives_markers_exposures_and_facts(raw):
    with_reading(raw)
    layer = pull_layer("vexcel", HOUSE)
    ortho = layer.evidence["ev_vx_ortho"]
    assert ortho.uri == f"/api/house-viewer/vexcel/{HOUSE}/aerial.jpg"
    assert ortho.locator["images_stored"] and ortho.locator["vision_model"] == "test-vision"
    assert [(x["side"], x["distance_m"], x["evidence_ids"]) for x in layer.exposures] == [
        ("east", 10, ["ev_vx_ortho"]), ("east", 2, ["ev_vx_ortho"])]   # far and unsure neighbours left out
    assert all(x["confidence"] <= 0.6 and x["status"] == "inferred" for x in layer.exposures)
    labels = {m.id: m.label for m in layer.markers}
    assert labels["m_vx_roof_0"].startswith("Solar panels on the roof")
    assert labels["m_vx_site_1"].startswith("Pool (possible drafting water)")
    assert layer.markers[0].map_position["lat"] > 29.81   # panels sit north of the centre
    stories = layer.facts["building.stories_aerial"]
    assert (stories.value, stories.evidence_ids) == (2, ["ev_vx_oblique_east", "ev_vx_oblique_north"])  # south's 3: once
    assert layer.facts["building.footprint_aerial_m"].value == [20, 20]
    assert layer.facts["building.roof_aerial"].value == "pitched"
    assert not any(m.kind == "entry" for m in layer.markers)   # entries only when there is no interior scan
    entries = [m for m in pull_layer("vexcel", HOUSE, exterior_entries=True).markers if m.kind == "entry"]
    assert [(m.label.split(" (")[0], m.confidence) for m in entries] == [("Front door on the south side", 0.5)]


@pytest.mark.parametrize("aerial_stories, messages", [
    (2, []),                                                                   # listing 2, scan 2, aerial 2
    (3, ["Listing: 2 floors; aerial: 3. Verify the number of floors.",
         "Scan: 2 floors; aerial: 3. Verify the number of floors (a basement is not visible from the air)."]),
])
def test_aerial_stories_are_checked_against_listing_and_scan(raw, aerial_stories, messages):
    reading = json.loads(json.dumps(SYNTHETIC_READING))
    for v in ("oblique-north", "oblique-east", "oblique-south"):
        reading["views"][v]["stories"] = aerial_stories
    with_reading(raw, reading)
    found = [c for c in load_dossier(HOUSE).contradictions if "aerial" in c.message]
    assert [c.message for c in found] == messages
    if found:
        assert [v.layer for v in found[0].values] == ["costar", "vexcel"]


def test_read_aerial_evidence_can_make_a_consideration_cross_layer(raw):
    from services.agents.brief import ConsiderationDraft, observed_evidence

    with_reading(raw)
    d = load_dossier(HOUSE)
    assert {"ev_vx_ortho", "ev_vx_oblique_north"} <= observed_evidence(d)
    record = FusionEngine("inc_h1").record
    record.confirmed_building_id = HOUSE
    exposure = ConsiderationDraft(category="exposure", title="Consider the house 10 m east",
                                  rationale="Two-storey house; neighbour 10 m east.",
                                  evidence_ids=["ev_cs_listing", "ev_vx_ortho"])
    assert build_brief(record, d, [exposure]).considerations[0].cross_layer is True


def test_read_building_reads_each_image_once(raw, monkeypatch):
    from services.agents import aerial

    with_reading(raw)
    (raw / HOUSE / "derived" / "aerial_reading.json").unlink()
    calls = []

    def fake(kind, schema, messages, retries=2):
        calls.append(schema.__name__)
        return schema.model_validate(SYNTHETIC_READING["views"]["ortho" if schema is aerial.OrthoReading
                                                                  else "oblique-north"])

    monkeypatch.setattr(aerial, "structured", fake)
    monkeypatch.setattr(aerial, "configured", lambda kind: True)
    for f in ("ortho", "oblique-north", "oblique-east", "oblique-south", "oblique-west"):
        (raw / HOUSE / "vexcel" / f"{f}.jpg").write_bytes(b"\xff\xd8fake")
    reading = aerial.read_building(HOUSE, root=raw)
    assert sorted(calls) == ["ObliqueReading"] * 4 + ["OrthoReading"] and set(reading["views"]) == {
        "ortho", "oblique-north", "oblique-east", "oblique-south", "oblique-west"}
    assert aerial.read_building(HOUSE, root=raw) == reading and len(calls) == 5   # stored: no second pass
    monkeypatch.setattr(aerial, "configured", lambda kind: False)
    (raw / HOUSE / "derived" / "aerial_reading.json").unlink()
    assert aerial.read_building(HOUSE, root=raw) is None   # no vision model: nothing written


def test_repeated_roof_features_are_counted_and_cut_outlines_have_no_footprint(raw):
    reading = json.loads(json.dumps(SYNTHETIC_READING))
    ortho = reading["views"]["ortho"]
    ortho["target"] = [200, 0, 800, 1000]   # a warehouse wider than the image
    ortho["roof_features"] = [_seen([300 + 10 * i, 300, 310 + 10 * i, 310], 0.8 - i / 100, kind="hvac_unit",
                                    label="HVAC unit") for i in range(18)]
    with_reading(raw, reading)
    layer = pull_layer("vexcel", HOUSE)
    [roof] = [m for m in layer.markers if m.id.startswith("m_vx_roof")]
    assert roof.label == "18 x HVAC unit on the roof (aerial: HVAC unit)" and roof.confidence == 0.6
    assert "building.footprint_aerial_m" not in layer.facts
