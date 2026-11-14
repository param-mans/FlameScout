from datetime import date

from flamescout_schemas import Fact, Marker
from services.agents.tools import rules

CFG = rules.load_config()


def fact(key, value, conf=0.9, ev=("ev_x",)):
    return Fact(key=key, value=value, status="verified", confidence=conf, evidence_ids=list(ev))


def marker(kind, label, floor=1, conf=0.9):
    return Marker(id=f"{label}{floor}", kind=kind, label=label, floor=floor, status="verified",
                  confidence=conf, evidence_ids=[f"ev_{label}{floor}"])


def test_r01_height_and_missing():
    f, ev = rules.r01_height(fact("building.stories", 8), CFG)
    assert f.value == 28.0 and f.status == "inferred"
    assert ev.id in f.evidence_ids and "ev_x" in f.evidence_ids
    assert rules.r01_height(None, CFG) is None
    assert rules.r01_height(fact("building.stories", "eight"), CFG) is None


def test_r02_reach_unknown_without_config():
    h, _ = rules.r01_height(fact("building.stories", 8), CFG)
    f, _ = rules.r02_reach(h, {**CFG, "apparatus": {"aerial_reach_m": None}})
    assert f.value["reachable"] == "unknown" and f.status == "unverified"


def test_r02_reach_yes_and_no():
    h, _ = rules.r01_height(fact("building.stories", 8), CFG)   # 28 m
    cfg = {**CFG, "apparatus": {"aerial_reach_m": 30.0, "setback_m": 6.0}}
    assert rules.r02_reach(h, cfg)[0].value["reachable"] == "yes"
    cfg["apparatus"]["aerial_reach_m"] = 25.0
    assert rules.r02_reach(h, cfg)[0].value["reachable"] == "no"


def test_r03_bands():
    assert rules.r03_exposure_band(5, CFG) == "close"
    assert rules.r03_exposure_band(20, CFG) == "moderate"
    assert rules.r03_exposure_band(50, CFG) == "low"
    assert rules.r03_exposure_band(None, CFG) == "unknown"


def test_r04_hazards_from_tenants_and_use():
    tenants = [fact("tenant", {"name": "Volt E-Bikes", "category": "E-bike retail"}, ev=("ev_t1",)),
               fact("tenant", {"name": "Reyes LLP", "category": "Office - legal"}, ev=("ev_t2",))]
    out = rules.r04_hazards(fact("building.use", "Mixed-use residential over commercial"), tenants, CFG)
    keys = {f.key for f, _ in out}
    assert keys == {"hazard.lithium_ion", "hazard.sleeping_occupants"}
    li = next(f for f, _ in out if f.key == "hazard.lithium_ion")
    assert li.status == "inferred" and "ev_t1" in li.evidence_ids and "ev_t2" not in li.evidence_ids


def test_r04_word_prefix_not_substring():
    # "collaborative" must not trigger "lab"-style substring hits; "label" must not hit anything
    tenants = [fact("tenant", {"name": "Collaborative Label Co", "category": "Office"})]
    assert rules.r04_hazards(None, tenants, CFG) == []
    assert rules.r04_hazards(None, [], CFG) == []


def test_r05_egress_counts_and_flag():
    ms = [marker("stair", "Stair A", f) for f in (1, 2, 3)] + [marker("exit", "Front door")]
    f, _ = rules.r05_egress(ms, True, CFG)
    assert f.value["stairwells"] == 1 and f.value["exits"] == 1
    assert "Only 1 stairwell" in f.value["flag"]
    ms.append(marker("stair", "Stair B", 1))
    assert rules.r05_egress(ms, True, CFG)[0].value["flag"] is None


def test_r05_no_scan_is_unknown():
    f, _ = rules.r05_egress([], False, CFG)
    assert f.status == "unverified" and f.value["stairwells"] is None


def test_r06_staleness_bands():
    out = rules.r06_staleness("2025-09-20T00:00:00Z", date(2026, 10, 1), CFG, today=date(2026, 11, 14))
    assert out["scan_age_days"] == 420 and out["scan_band"] == "aging"
    assert out["costar_band"] == "fresh"
    assert rules.r06_staleness(None, None, CFG)["scan_band"] == "unknown"


def test_r07_occupant_load():
    f, _ = rules.r07_occupant_load(fact("building.area_sqft", 15000), fact("building.use", "Office"), CFG)
    assert f.value == 100 and f.status == "inferred"
    assert rules.r07_occupant_load(None, fact("building.use", "Office"), CFG) is None
    assert rules.r07_occupant_load(fact("building.area_sqft", 1000), fact("building.use", "Spaceport"), CFG) is None


def test_r09_confidence():
    assert rules.r09_confidence([0.9, 0.7], "inferred", CFG) == 0.7
    assert rules.r09_confidence([0.9], "reported", CFG) == 0.6
    assert rules.r09_confidence([0.9], "unverified", CFG) == 0.0
    assert rules.r09_confidence([], "inferred", CFG) == 0.0


def test_haversine():
    assert abs(rules.haversine_m(34.0, -118.0, 34.001, -118.0) - 111.2) < 0.5
