import json
from pathlib import Path

import pytest

from flamescout_schemas import Brief, CallSegment
from services.agents.brief import (ConsiderationDraft, build_brief, check_considerations, merge_evidence,
                                   rejected_count)
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
                rationale="Stair B discharges to the rear alley, which has vehicle access.",
                evidence_ids=["ev_scan_fp_1", "ev_aerial_2"])  # matterport + vexcel
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
    assert (brief.freshness["prefetched"], brief.freshness["prefetched_at"]) == (False, None)
    at = brief.generated_at
    warm = build_brief(incident, dossier, [], prefetched_at=at)
    assert (warm.freshness["prefetched"], warm.freshness["prefetched_at"]) == (True, at.isoformat())


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


def test_changes_since_last_is_a_deterministic_diff(dossier):
    segments = [CallSegment(**s) for s in json.loads((CALLS / "s2.json").read_text())["segments"]]
    eng = FusionEngine("inc_s2")
    eng.ingest_all(s for s in segments if s.call_id == "c1")
    eng.confirm("fake_wilshire_tower")
    v1 = build_brief(eng.record.model_copy(deep=True), dossier, [draft()])
    assert v1.changes_since_last == [] and v1.verifier_rejections == 0

    eng.ingest_all(s for s in segments if s.call_id == "c2")
    v2 = build_brief(eng.record, dossier, [draft()], version=2, previous=v1, verifier_rejections=3)
    assert v2.changes_since_last == [
        "Floor contradicted: 3 vs 4 (Call 2)",
        "Visible added: flames (Call 2)",
        "New: Building name Wilshire Tower (Call 2)",
        "New: Access notes back stairs (Call 2)",
    ]
    assert (v2.version, v2.brief_id, v2.verifier_rejections) == (2, "inc_s2_v2", 3)

    v3 = build_brief(eng.record, dossier, [draft(title="Consider Stair A instead")], version=3, previous=v2)
    assert v3.changes_since_last == ["Considerations redrafted: 1 (was 1)"]


def test_rejected_count(incident, dossier):
    evidence = merge_evidence(incident, dossier)
    assert rejected_count([draft(), draft(title="Go"), draft(evidence_ids=["ev_made_up"])], evidence) == 2
    assert rejected_count([draft()] * 7, evidence) == 2  # two beyond the maximum
    assert rejected_count([draft()], evidence) == 0
    assert rejected_count([draft(evidence_ids=["ev_scan_fp_4"])], evidence) == 1  # no cross-layer one


def test_cross_layer_is_flagged_and_required(incident, dossier):
    evidence = merge_evidence(incident, dossier)
    single = draft(category="hazard", evidence_ids=["ev_costar_3"])
    with_call = draft(evidence_ids=["ev_scan_fp_1", "ev_call_2"])  # a call is not a data layer
    brief = build_brief(incident, dossier, [draft(), single, with_call])
    assert [c.cross_layer for c in brief.considerations] == [True, False, False]

    errors = check_considerations([single, with_call], evidence)
    assert len(errors) == 1 and "combines data layers" in errors[0]
    with pytest.raises(ValueError, match="combines data layers"):
        build_brief(incident, dossier, [single])


def test_cross_layer_rule_needs_two_layers_and_a_draft(incident, dossier):
    assert build_brief(incident, dossier, []).considerations == []  # facts-only fallback stays legal
    costar_only = {k: e for k, e in merge_evidence(incident, dossier).items() if e.source in ("costar", "call")}
    assert check_considerations([draft(category="hazard", evidence_ids=["ev_costar_3"])], costar_only) == []
