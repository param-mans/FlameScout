import json
import shutil

import pytest

from services.agents.dossier import (LAYERS, digest, load_dossier, merge_layers, pull_layer,
                                     unresolved_evidence)
from services.agents.providers import FIXTURES, get_providers

BUILDING = "fake_wilshire_tower"


def test_each_layer_is_tagged_with_its_own_source():
    for name in LAYERS:
        layer = pull_layer(name, BUILDING)
        assert layer.evidence and {e.source for e in layer.evidence.values()} == {name}
        assert unresolved_evidence(layer) == []
    assert pull_layer("costar", BUILDING).markers == []
    assert {m.id for m in pull_layer("vexcel", BUILDING).markers} == {"m_roof", "m_alley_access"}


def test_marker_seen_on_two_floorplans_is_one_marker():
    stair_b = next(m for m in pull_layer("matterport", BUILDING).markers if m.id == "m_stair_b")
    assert stair_b.evidence_ids == ["ev_scan_fp_1", "ev_scan_fp_4"]


def test_merge_builds_a_valid_dossier():
    d = load_dossier(BUILDING)
    assert (d.building_id, d.address, d.scan_available) == (BUILDING, "1200 Wilshire Boulevard", True)
    assert {e.source for e in d.evidence.values()} == set(LAYERS)
    assert unresolved_evidence(d) == [] and len(d.markers) == 10 and len(d.exposures) == 2
    assert all(x["evidence_ids"] for x in d.exposures)
    for f in [*d.facts.values(), *d.tenants, *d.markers]:
        assert f.evidence_ids or f.status == "unverified", f
    assert d.matterport_model_id is None  # fixture ids are not embeddable models


def test_merge_needs_costar_and_rejects_clashing_evidence():
    layers = {name: pull_layer(name, BUILDING) for name in LAYERS}
    with pytest.raises(ValueError, match="without the costar layer"):
        merge_layers(BUILDING, [layers["matterport"], layers["vexcel"]])
    layers["vexcel"].evidence["ev_costar_1"] = layers["costar"].evidence["ev_costar_1"]
    with pytest.raises(ValueError, match="more than one layer"):
        merge_layers(BUILDING, layers.values())


@pytest.fixture
def fixtures_copy(tmp_path):
    shutil.copytree(FIXTURES / BUILDING, tmp_path / BUILDING)
    return tmp_path


def test_building_without_a_scan_still_merges(fixtures_copy):
    (fixtures_copy / BUILDING / "matterport.json").write_text(json.dumps({"model": None}))
    d = load_dossier(BUILDING, get_providers(fixtures_copy))
    assert not d.scan_available and d.scan_captured_at is None
    assert {m.id for m in d.markers} == {"m_roof", "m_alley_access"}


def test_evidence_from_the_wrong_source_is_rejected(fixtures_copy):
    path = fixtures_copy / BUILDING / "vexcel.json"
    data = json.loads(path.read_text())
    data["aerials"][0]["evidence"]["source"] = "matterport"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="evidence from another source"):
        pull_layer("vexcel", BUILDING, get_providers(fixtures_copy))


def test_digest_keeps_evidence_ids():
    costar = pull_layer("costar", BUILDING)
    text = digest(costar)
    assert all(e in text for e in costar.evidence) and "[no evidence]" in text  # sprinklered is unverified
    assert "Interior scan: available" in digest(pull_layer("matterport", BUILDING))


def test_unknown_building_is_an_error():
    with pytest.raises(FileNotFoundError, match="No CoStar fixture"):
        load_dossier("no_such_building")


DEPOT = "fake_quarry_depot"


def test_unscanned_building_takes_its_entries_from_aerial_imagery():
    assert not pull_layer("matterport", DEPOT).scan_available
    plain = pull_layer("vexcel", DEPOT)
    assert [m.id for m in plain.markers] == ["m_qd_yard"] and not plain.exterior_entries
    with_entries = pull_layer("vexcel", DEPOT, exterior_entries=True)
    assert with_entries.exterior_entries
    assert {m.id: m.confidence for m in with_entries.markers if m.kind == "entry"} == {
        "m_qd_ext_roller": 0.45, "m_qd_ext_front": 0.4}

    d = load_dossier(DEPOT)  # the no-scan policy in one place
    assert not d.scan_available and {m.kind for m in d.markers} == {"other", "entry"}
    assert {e.source for e in d.evidence.values()} == {"costar", "vexcel"}


def test_scanned_building_does_not_get_aerial_entries():
    d = load_dossier(BUILDING)
    assert d.scan_available and {m.id for m in d.markers if m.kind == "entry"} == {"m_entry_main", "m_entry_rear"}
