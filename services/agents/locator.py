"""Candidate buildings for an incident. Plain function, no LLM.

Asks the CoStar adapter for properties matching what callers said about the
location (address and/or building name) and scores them. The dispatcher picks
from the result; the orchestrator only passes it along.
"""
from __future__ import annotations

from typing import Optional

from flamescout_schemas import BuildingCandidate, IncidentRecord

from .providers import Providers, get_providers, tokens


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
