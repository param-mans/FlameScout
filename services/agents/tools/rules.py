"""Deterministic rules engine (PRD Section 9). Pure Python, no LLM calls.

Each rule takes plain inputs and returns its output plus an `Evidence` of kind
"rule_output" whose locator names the rule and the evidence ids it consumed, so
the UI can trace a computed number back to the records behind it.
Missing input -> "unknown" output, never a guess.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from flamescout_schemas import Evidence, Fact, FactStatus, Marker

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "rules.yaml"


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or CONFIG_PATH).read_text())


def rule_evidence(rule_id: str, inputs: Iterable[str], detail: dict | None = None,
                  suffix: str = "") -> Evidence:
    eid = f"ev_rule_{rule_id.lower().replace('-', '')}{'_' + suffix if suffix else ''}"
    return Evidence(id=eid, kind="rule_output", source="rules",
                    locator={"rule": rule_id, "inputs": list(inputs), **(detail or {})})


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- R-01 height estimate ----------
FT_TO_M = 0.3048


def r01_height(stories: Optional[Fact], cfg: dict,
               height_ft: Optional[Fact] = None) -> Optional[tuple[Fact, Evidence]]:
    """Recorded height (CoStar buildingHeightInFeet) wins; else stories x floor height."""
    if height_ft is not None and isinstance(height_ft.value, (int, float)) and height_ft.value > 0:
        h = round(float(height_ft.value) * FT_TO_M, 1)
        ev = rule_evidence("R-01", height_ft.evidence_ids,
                           {"formula": f"{height_ft.value} ft x {FT_TO_M} m/ft (recorded height)"})
        return Fact(key="building.height_est_m", value=h, status="verified",
                    confidence=height_ft.confidence, evidence_ids=[*height_ft.evidence_ids, ev.id],
                    as_of=height_ft.as_of), ev
    if stories is None or not isinstance(stories.value, (int, float)) or stories.value <= 0:
        return None
    h = round(float(stories.value) * cfg["floor_height_m"], 1)
    ev = rule_evidence("R-01", stories.evidence_ids,
                       {"formula": f"{stories.value} stories x {cfg['floor_height_m']} m"})
    fact = Fact(key="building.height_est_m", value=h, status="inferred",
                confidence=r09_confidence([stories.confidence], "inferred", cfg),
                evidence_ids=[*stories.evidence_ids, ev.id], as_of=stories.as_of)
    return fact, ev


# ---------- R-02 apparatus reach ----------
def r02_reach(height: Optional[Fact], cfg: dict) -> tuple[Fact, Evidence]:
    app = cfg.get("apparatus") or {}
    reach, setback = app.get("aerial_reach_m"), app.get("setback_m") or 0.0
    inputs = height.evidence_ids if height else []
    if height is None or reach is None:
        why = "no height estimate" if height is None else "no apparatus config"
        ev = rule_evidence("R-02", inputs, {"result": "unknown", "why": why})
        return Fact(key="building.aerial_reach", value={"reachable": "unknown", "margin_m": None},
                    status="unverified", confidence=0.0, evidence_ids=[ev.id]), ev
    needed = math.hypot(height.value, setback)
    margin = round(reach - needed, 1)
    ev = rule_evidence("R-02", inputs, {"reach_m": reach, "needed_m": round(needed, 1)})
    return Fact(key="building.aerial_reach",
                value={"reachable": "yes" if margin >= 0 else "no", "margin_m": margin},
                status="inferred", confidence=height.confidence,
                evidence_ids=[*height.evidence_ids, ev.id]), ev


# ---------- R-03 exposure band ----------
def r03_exposure_band(distance_m: Optional[float], cfg: dict) -> str:
    if distance_m is None:
        return "unknown"
    bands = cfg["exposure_bands_m"]
    if distance_m < bands["close"]:
        return "close"
    if distance_m < bands["moderate"]:
        return "moderate"
    return "low"


# ---------- R-04 occupancy hazards ----------
def r04_hazards(use: Optional[Fact], tenants: list[Fact], cfg: dict) -> list[tuple[Fact, Evidence]]:
    """One inferred hazard Fact per flag; evidence = the record(s) that triggered it."""
    sources: list[tuple[str, list[str], str]] = []   # (text, evidence_ids, description)
    if use is not None and use.value:
        sources.append((str(use.value), use.evidence_ids, f"Building use: {use.value}"))
    for t in tenants:
        v = t.value if isinstance(t.value, dict) else {"name": str(t.value)}
        text = f"{v.get('name', '')} {v.get('category', '')}"
        desc = f"Tenant: {v.get('name', '?')}" + (f" ({v['category']})" if v.get("category") else "")
        sources.append((text, t.evidence_ids, desc))

    out: list[tuple[Fact, Evidence]] = []
    for flag in cfg["hazard_map"]:
        hits = [(eids, desc) for text, eids, desc in sources
                if any(re.search(rf"\b{re.escape(m)}", text.lower()) for m in flag["match"])]
        if not hits:
            continue
        inputs = sorted({e for eids, _ in hits for e in eids})
        ev = rule_evidence("R-04", inputs, {"flag": flag["id"]}, suffix=flag["id"])
        out.append((Fact(key=f"hazard.{flag['id']}",
                         value={"label": flag["label"], "because": [d for _, d in hits]},
                         status="inferred", confidence=cfg["hazard_confidence"],
                         evidence_ids=[*inputs, ev.id]), ev))
    return out


# ---------- R-05 egress inventory ----------
def r05_egress(markers: list[Marker], scan_available: bool, cfg: dict) -> tuple[Fact, Evidence]:
    if not scan_available:
        ev = rule_evidence("R-05", [], {"result": "unknown", "why": "no interior scan"})
        return Fact(key="egress.inventory", value={"stairwells": None, "exits": None, "flag": None},
                    status="unverified", confidence=0.0, evidence_ids=[ev.id]), ev
    stairs = [m for m in markers if m.kind == "stair"]
    exits = [m for m in markers if m.kind in ("exit", "entry")]
    stairwells = sorted({m.label for m in stairs})
    by_floor: dict[str, list[str]] = {}
    for m in stairs:
        by_floor.setdefault(str(m.floor) if m.floor is not None else "?", []).append(m.label)
    n = len(stairwells)
    flag = (f"Only {n} stairwell(s) identified - verify on arrival"
            if n < cfg["min_stairwells"] else None)
    inputs = sorted({e for m in stairs + exits for e in m.evidence_ids})
    ev = rule_evidence("R-05", inputs, {"min_stairwells": cfg["min_stairwells"]})
    conf = r09_confidence([m.confidence for m in stairs] or [0.5], "inferred", cfg)
    return Fact(key="egress.inventory",
                value={"stairwells": n, "stairwell_labels": stairwells,
                       "stairs_by_floor": {k: sorted(set(v)) for k, v in by_floor.items()},
                       "exits": len(exits), "flag": flag},
                status="inferred", confidence=conf, evidence_ids=[*inputs, ev.id]), ev


# ---------- R-06 staleness ----------
def _age_days(when: Any, today: date) -> Optional[int]:
    if when is None:
        return None
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    d = when.date() if isinstance(when, datetime) else when
    return (today - d).days


def _band(days: Optional[int], limits: dict) -> str:
    if days is None:
        return "unknown"
    return "fresh" if days < limits["fresh"] else "aging" if days < limits["aging"] else "stale"


def r06_staleness(scan_captured_at: Any, costar_as_of: Any, cfg: dict,
                  today: Optional[date] = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    scan_days, costar_days = _age_days(scan_captured_at, today), _age_days(costar_as_of, today)
    lim = cfg["staleness_days"]
    return {"scan_age_days": scan_days, "scan_band": _band(scan_days, lim["scan"]),
            "costar_as_of": str(costar_as_of) if costar_as_of else None,
            "costar_age_days": costar_days, "costar_band": _band(costar_days, lim["costar"])}


# ---------- R-07 occupant load (P2) ----------
def r07_occupant_load(area: Optional[Fact], use: Optional[Fact],
                      cfg: dict) -> Optional[tuple[Fact, Evidence]]:
    if area is None or use is None or not isinstance(area.value, (int, float)):
        return None
    table = cfg["occupant_load_sqft_per_person"]
    key = next((k for k in table if k in str(use.value).lower()), None)
    if key is None:
        return None
    est = int(area.value // table[key])
    inputs = [*area.evidence_ids, *use.evidence_ids]
    ev = rule_evidence("R-07", inputs, {"formula": f"{area.value} sqft / {table[key]} sqft per person"})
    return Fact(key="building.occupant_load_est", value=est, status="inferred",
                confidence=r09_confidence([area.confidence, use.confidence], "inferred", cfg) * 0.8,
                evidence_ids=[*inputs, ev.id]), ev


# ---------- R-09 confidence aggregation ----------
def r09_confidence(confidences: Iterable[float], status: FactStatus, cfg: dict) -> float:
    vals = [c for c in confidences if c is not None]
    conf = min(vals) if vals else 0.0
    if status == "reported":
        conf = min(conf, cfg["reported_confidence_cap"])
    if status == "unverified":
        conf = 0.0
    return round(max(0.0, min(1.0, conf)), 3)
