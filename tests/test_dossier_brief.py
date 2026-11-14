from pathlib import Path

import pytest

from services.agents import brief as B
from services.agents import critic, locator
from services.agents.dossier import build_dossier
from services.agents.extract import _LLMFact, validate_llm_facts
from services.agents.fusion import FusionEngine
from services.agents.tools.providers import get_providers
from services.agents.tools.rules import load_config

from .conftest import FIXTURES, load_script

CFG = load_config()
P = get_providers("fixture", FIXTURES, {})


@pytest.fixture
def dossier(tmp_path):
    return lambda bid: build_dossier(bid, P, CFG, tmp_path)


def incident(script, n=None):
    segs = load_script(script)
    return FusionEngine(f"inc_{script}").ingest_all(segs[:n] if n else segs)


def all_ids_resolve(d):
    things = list(d.facts.values()) + d.tenants + d.markers
    for t in things:
        assert t.evidence_ids, t
        assert all(e in d.evidence for e in t.evidence_ids), t
    for x in d.exposures:
        assert all(e in d.evidence for e in x["evidence_ids"])


def test_scanned_dossier_has_evidence_everywhere(dossier):
    d = dossier("wilshire_tower")
    assert d.scan_available and d.matterport_model_id == "fx-wilshire-001"
    assert d.facts["building.stories"].value == 8
    assert d.facts["building.height_est_m"].value == 28.0
    assert d.facts["egress.inventory"].value["stairwells"] == 2
    assert {m.kind for m in d.markers} >= {"stair", "entry", "exit", "roof_access"}
    all_ids_resolve(d)


def test_exposure_distances_computed_from_coordinates(dossier):
    d = dossier("wilshire_tower")
    east = next(x for x in d.exposures if "east" in x["label"])
    assert 7 <= east["distance_m"] <= 9 and east["band"] == "close"
    assert [x["distance_m"] for x in d.exposures] == sorted(x["distance_m"] for x in d.exposures)


def test_s4_no_scan_abstains(dossier):
    d = dossier("maple_lofts")
    assert not d.scan_available
    assert not [m for m in d.markers if m.kind in ("stair", "exit", "entry", "elevator")]
    assert d.facts["egress.inventory"].status == "unverified"
    b = B.build_brief(incident("S4"), d, 1, CFG)
    assert any("No interior scan" in g for g in b.gaps)
    assert all(c.confidence <= CFG["no_scan_confidence_cap"] for c in b.considerations)
    assert {"hazard.lithium_ion", "hazard.sleeping_occupants"} <= {h.key for h in b.hazards}
    assert critic.check_brief(b) == []


def test_s3_ambiguous_name_gives_two_candidates():
    cands = locator.locate(incident("S3"), P)
    assert {c.building_id for c in cands} >= {"harbor_plaza_ocean", "harbor_plaza_pine"}


def test_s2_locates_single_building():
    cands = locator.locate(incident("S2"), P)
    assert cands[0].building_id == "wilshire_tower" and len(cands) == 1


def test_s1_brief_is_advisory_and_supported(dossier):
    b = B.build_brief(incident("S1"), dossier("wilshire_tower"), 1, CFG)
    assert 0 < len(b.considerations) <= 5
    assert [c.rank for c in b.considerations] == list(range(1, len(b.considerations) + 1))
    assert all(c.title.startswith("Consider ") for c in b.considerations)
    assert critic.check_brief(b) == []
    assert b.size_up["incident.floor"].value == 5


def test_s2_v1_to_v2_diff_and_contradiction(dossier):
    d = dossier("wilshire_tower")
    v1 = B.build_brief(incident("S2", 4), d, 1, CFG)
    v2 = B.build_brief(incident("S2"), d, 2, CFG, prev=v1)
    assert v2.size_up["incident.floor"].status == "contradicted"
    assert v2.size_up["incident.floor"].value == [3, 4]
    assert any(line.startswith("! incident.floor") and "(c2)" in line for line in v2.changes_since_last)
    assert any("confirming the fire floor" in c.title for c in v2.considerations)
    assert any("Unresolved: incident.floor" in g for g in v2.gaps)
    # caller's "back stairs" + scan's rear stair: fused in one consideration with both evidences
    c = next(c for c in v2.considerations if "back stairs" in c.title)
    assert "Stair B" in c.rationale
    assert any(e.startswith("ev_call") for e in c.evidence_ids) and any(e.startswith("ev_mp") for e in c.evidence_ids)
    assert critic.check_brief(v2) == []


def test_ranking_keeps_one_per_category_before_filling(dossier):
    v2 = B.build_brief(incident("S2"), dossier("wilshire_tower"), 1, CFG)
    cats = [c.category for c in v2.considerations]
    assert {"life_safety", "access", "hazard", "exposure"} <= set(cats)
    assert cats == sorted(cats, key=B.PRIORITY.get)


def test_critic_catches_orders_and_missing_evidence(dossier):
    b = B.build_brief(incident("S1"), dossier("wilshire_tower"), 1, CFG)
    bad = b.considerations[0].model_copy(update={"title": "Send crews immediately", "evidence_ids": ["nope"]})
    b.considerations = [bad]
    msgs = {p.message.split(":")[0] for p in critic.check_brief(b)}
    assert "unresolved evidence ids" in msgs and "order language" in msgs
    assert "title is not advisory ('Consider ...')" in msgs


def test_llm_facts_gate_drops_hallucinations():
    text = "Fire's on the fourth floor, someone is trapped"
    out = validate_llm_facts(text, [
        _LLMFact(key="incident.floor", value="4", quote="fourth floor"),
        _LLMFact(key="people.trapped", value="yes", quote="someone is trapped"),
        _LLMFact(key="hazards.reported", value="gas", quote="smell of gas"),      # not said
        _LLMFact(key="building.stories", value="9", quote="fourth floor"),        # unknown key
    ])
    assert [(f.key, f.value) for f in out] == [("incident.floor", 4), ("people.trapped", True)]
