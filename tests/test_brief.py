import json
from pathlib import Path

import pytest

from flamescout_schemas import Brief, CallSegment
from services.agents.brief import ConsiderationDraft, build_brief, check_considerations, merge_evidence
from services.agents.dossier import load_dossier
from services.agents.fusion import FusionEngine

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"


@pytest.fixture
def incident():
    data = json.loads((CALLS / "s2.json").read_text())
    rec = FusionEngine("inc_s2").ingest_all(CallSegment(**s) for s in data["segments"])
    rec.confirmed_building_id = "fake_wilshire_tower"
    return rec


@pytest.fixture
def dossier():
    return load_dossier("fake_wilshire_tower")


def draft(**kw) -> ConsiderationDraft:
    base = dict(category="access", title="Consider Stair B from the rear alley",
                rationale="A caller reported someone shouting from the back stairs.",
                evidence_ids=["ev_scan_fp_4"])
    return ConsiderationDraft(**{**base, **kw})


def test_brief_is_valid_and_every_id_resolves(incident, dossier):
    brief = build_brief(incident, dossier, [draft(), draft(category="hazard", evidence_ids=["ev_costar_3"])])
    brief = Brief.model_validate_json(brief.model_dump_json())  # what the UI will parse
    assert brief.location_confirmed and brief.brief_id == "inc_s2_v1"
    assert [(c.id, c.rank) for c in brief.considerations] == [("c1", 1), ("c2", 2)]
    cited = [e for x in [*brief.size_up.values(), *brief.markers, *brief.hazards, *brief.considerations]
             for e in x.evidence_ids]
    assert cited and all(e in brief.evidence for e in cited)
    assert {f.key for f in brief.hazards} == {"hazard.commercial_kitchen", "hazard.lithium_batteries"}
    assert "building.stories" in brief.size_up and "incident.floor" in brief.size_up


def test_confidence_is_the_weakest_cited_item(incident, dossier):
    brief = build_brief(incident, dossier, [draft(evidence_ids=["ev_costar_1", "ev_scan_fp_4"])])
    assert brief.considerations[0].confidence == 0.55  # m_standpipe cites ev_scan_fp_4


def test_gaps_and_freshness_are_computed(incident, dossier):
    brief = build_brief(incident, dossier, [])
    assert any("incident.floor" in g and "3 vs 4" in g for g in brief.gaps)
    assert "Unverified: building.sprinklered" in brief.gaps
    assert brief.freshness["scan_age_days"] >= 0
    assert brief.freshness["costar_as_of"] == "2026-09-01"


@pytest.mark.parametrize("bad, message", [
    (dict(title="Attack via Stair B"), 'must start with "Consider "'),
    (dict(title="consider Stair B"), 'must start with "Consider "'),
    (dict(evidence_ids=[]), "no evidence_ids"),
    (dict(evidence_ids=["ev_made_up"]), "unknown evidence ids"),
    (dict(rationale="  "), "rationale is empty"),
])
def test_bad_considerations_are_rejected(incident, dossier, bad, message):
    errors = check_considerations([draft(**bad)], merge_evidence(incident, dossier))
    assert any(message in e for e in errors)
    with pytest.raises(ValueError):
        build_brief(incident, dossier, [draft(**bad)])


def test_at_most_five_considerations(incident, dossier):
    evidence = merge_evidence(incident, dossier)
    assert check_considerations([draft()] * 5, evidence) == []
    assert any("maximum is 5" in e for e in check_considerations([draft()] * 6, evidence))


def test_evidence_id_collision_is_an_error(incident, dossier):
    dossier.evidence["ev_call_1"] = dossier.evidence["ev_costar_1"]
    with pytest.raises(ValueError, match="both incident and dossier"):
        build_brief(incident, dossier, [])
