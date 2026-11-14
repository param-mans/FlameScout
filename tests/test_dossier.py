import pytest

from services.agents.dossier import digest, load_dossier, unresolved_evidence

BUILDING = "fake_wilshire_tower"


def test_fixture_is_a_valid_dossier_with_resolving_evidence():
    d = load_dossier(BUILDING)
    assert d.building_id == BUILDING
    assert unresolved_evidence(d) == []
    for f in [*d.facts.values(), *d.tenants, *d.markers]:
        assert f.evidence_ids or f.status == "unverified", f


def test_digest_keeps_evidence_ids():
    d = load_dossier(BUILDING)
    text = digest(d)
    assert all(e in text for e in d.evidence)
    assert "[no evidence]" in text  # building.sprinklered is unverified


def test_unknown_or_unsafe_building_id_is_an_error():
    with pytest.raises(FileNotFoundError, match="No dossier fixture"):
        load_dossier("no_such_building")
    with pytest.raises(ValueError, match="Bad building id"):
        load_dossier("../calls")
