"""Candidate buildings for an incident. Plain functions, no LLM (PRD FR-3).

- find_candidates(): the orchestrator's locator. Asks the CoStar adapter (providers.py) for
  properties matching what callers said (address and/or building name) and scores them.
- locate(): the services/api pipeline's locator (tools/providers.py). CoStar search on the
  reported name and address; when a geocoder is configured, candidates within GEO_MATCH_M of
  the geocoded address get a boost.

The dispatcher must confirm one; nothing downstream runs before that.
"""
from __future__ import annotations

import logging
from typing import Optional

from flamescout_schemas import BuildingCandidate, IncidentRecord, LocationCandidate

from .providers import Providers, get_providers, tokens
from .tools.providers import Providers as ToolProviders
from .tools.rules import haversine_m

log = logging.getLogger("flamescout.locator")
GEO_MATCH_M = 150.0
MAX_CANDIDATES = 5


def _similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def find_candidates(incident: IncidentRecord, providers: Optional[Providers] = None) -> list[BuildingCandidate]:
    """Best match first. Empty when callers gave no location or nothing matches."""
    costar = (providers or get_providers()).costar
    said = [str(incident.facts[k].value) for k in ("location.address", "location.name") if k in incident.facts]
    best: dict[str, BuildingCandidate] = {}
    for query in said:
        for p in costar.search_property(query):
            score = round(max(_similarity(query, p["address"]), _similarity(query, p["name"])), 2)
            if score > best.get(p["property_id"], BuildingCandidate(building_id="", name="", address="", score=0)).score:
                best[p["property_id"]] = BuildingCandidate(building_id=p["property_id"], name=p["name"],
                                                           address=p["address"], score=score)
    return sorted(best.values(), key=lambda c: (-c.score, c.building_id))


def queries(rec: IncidentRecord) -> list[tuple[str, str]]:
    out = []
    for key in ("location.name", "location.address"):
        f = rec.facts.get(key)
        if f is None:
            continue
        values = f.value if isinstance(f.value, list) else [f.value]
        out += [(key, str(v)) for v in values]
    return out


def locate(rec: IncidentRecord, providers: ToolProviders) -> list[LocationCandidate]:
    found: dict[str, dict] = {}
    for key, q in queries(rec):
        for r in providers.costar.search_property(q):
            c = found.setdefault(r["property_id"], {**r, "score": 0.0, "reasons": []})
            c["score"] = max(c["score"], float(r.get("score", 0.0)))
            c["reasons"].append(f"{key.split('.')[1]} '{q}' matched ({r.get('score', 0):.2f})")

    address = rec.facts.get("location.address")
    if providers.geocoder and address:
        try:
            hits = providers.geocoder.geocode(str(address.value))
        except Exception as e:  # noqa: BLE001
            log.warning("geocoder failed: %s", e)
            hits = []
        if hits:
            g = hits[0]
            for c in found.values():
                if c.get("lat") is not None and haversine_m(g["lat"], g["lon"], c["lat"], c["lon"]) <= GEO_MATCH_M:
                    c["score"] = min(1.0, c["score"] + 0.2)
                    c["reasons"].append("geocoded address within 150 m")

    ranked = sorted(found.values(), key=lambda c: -c["score"])[:MAX_CANDIDATES]
    return [LocationCandidate(building_id=c["property_id"], name=c.get("name"), address=c.get("address") or "",
                              coordinates={"lat": c["lat"], "lon": c["lon"]} if c.get("lat") is not None else {},
                              score=round(c["score"], 3), match_reasons=c["reasons"])
            for c in ranked]
