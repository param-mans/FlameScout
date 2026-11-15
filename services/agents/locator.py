"""Candidate buildings for an incident. Plain functions, no LLM (PRD FR-3).

- find_candidates(): the orchestrator's locator. Asks the CoStar adapter (providers.py) for
  properties matching what callers said (address and/or building name), scores them, says why
  each matched (match_reasons), and asks the Matterport adapter whether it has a scan. The house
  catalog (data/raw/catalog.csv, house_catalog.match: house number, street, city; spoken digits)
  is an extra source; a house found both ways keeps the better score.
- locate(): the services/api pipeline's locator (tools/providers.py). CoStar search on the
  reported name and address; when a geocoder is configured, candidates within GEO_MATCH_M of
  the geocoded address get a boost.

The dispatcher must confirm one; nothing downstream runs before that.
"""
from __future__ import annotations

import logging
from typing import Optional

from flamescout_schemas import BuildingCandidate, IncidentRecord, LocationCandidate

from . import house_catalog
from .providers import Providers, _raw, get_providers, tokens
from .tools.providers import Providers as ToolProviders
from .tools.rules import haversine_m

log = logging.getLogger("flamescout.locator")
GEO_MATCH_M = 150.0
MAX_CANDIDATES = 5


def _similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _reason(query: str, prop: dict) -> tuple[float, str]:
    """How well one thing a caller said matches a property, and why: its address or its name,
    whichever matches better (on a tie, the address)."""
    by_address, by_name = _similarity(query, prop["address"]), _similarity(query, prop["name"])
    if by_address >= by_name:
        reason = ("address exact match" if by_address == 1 else
                  f"address partial match: {prop['address']} ({by_address:.2f})")
        return by_address, reason
    reason = f"name match: {prop['name']}" if by_name == 1 else f"name partial match: {prop['name']} ({by_name:.2f})"
    return by_name, reason


def _house_number(address: str) -> Optional[str]:
    """The leading house number of an address, spoken digits included ("nine six six oh Fairdale" -> 9660)."""
    toks = house_catalog.normalize(address)
    return toks[0] if toks and toks[0].isdigit() else None


def _scan_available(providers: Providers, building_id: str) -> Optional[bool]:
    """Whether the Matterport adapter has a model for the building; None if it could not say. A catalog house
    without a folder: True (the catalog lists scanned houses; its plan is fetched only once it is confirmed)."""
    if house_catalog.get(building_id) is not None and _raw(building_id, "matterport", "plan.json") is None:
        return True
    try:
        return providers.matterport.find_model({"property_id": building_id}) is not None
    except Exception as e:  # noqa: BLE001 - informational only; the dossier step decides for real
        log.warning("Matterport lookup failed for %s: %s", building_id, e)
        return None


def find_candidates(incident: IncidentRecord, providers: Optional[Providers] = None) -> list[BuildingCandidate]:
    """Best match first. Empty when callers gave no location or nothing matches."""
    providers = providers or get_providers()
    said = [(k, str(incident.facts[k].value)) for k in ("location.address", "location.name") if k in incident.facts]
    found: dict[str, dict] = {}
    for key, query in said:
        number = _house_number(query) if key == "location.address" else None
        for p in providers.costar.search_property(query):
            if number and _house_number(p["address"]) not in (None, number):
                continue   # the caller said another house number: not this building
            score, reason = _reason(query, p)
            c = found.setdefault(p["property_id"], {"property": p, "score": 0.0, "reasons": []})
            c["score"] = max(c["score"], score)
            if reason not in c["reasons"]:
                c["reasons"].append(reason)
    if "location.address" in incident.facts:
        for m in house_catalog.match(str(incident.facts["location.address"].value)):
            c = found.get(m.listing_id)
            if c is None:
                p = {"property_id": m.listing_id, "name": m.row.get("address") or m.listing_id,
                     "address": house_catalog.full_address(m.row), "coordinates": {}}
                c = found.setdefault(m.listing_id, {"property": p, "score": 0.0, "reasons": []})
            c["score"] = max(c["score"], m.score)
            c["reasons"] += [r for r in m.reasons if r not in c["reasons"]]
    candidates = [BuildingCandidate(building_id=bid, name=c["property"]["name"], address=c["property"]["address"],
                                    coordinates=dict(c["property"].get("coordinates") or {}),
                                    score=round(c["score"], 2), match_reasons=c["reasons"],
                                    scan_available=_scan_available(providers, bid))
                  for bid, c in found.items() if c["score"] > 0]
    return sorted(candidates, key=lambda c: (-c.score, c.building_id))


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
