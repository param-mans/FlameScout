"""Locator (PRD FR-3): caller location facts -> ranked building candidates. Plain function.

CoStar property search on the reported name and address; when a geocoder is
configured, candidates within GEO_MATCH_M of the geocoded address get a boost.
The dispatcher must confirm one; nothing downstream runs before that.
"""
from __future__ import annotations

import logging

from flamescout_schemas import IncidentRecord, LocationCandidate

from .tools.providers import Providers
from .tools.rules import haversine_m

log = logging.getLogger("flamescout.locator")
GEO_MATCH_M = 150.0
MAX_CANDIDATES = 5


def queries(rec: IncidentRecord) -> list[tuple[str, str]]:
    out = []
    for key in ("location.name", "location.address"):
        f = rec.facts.get(key)
        if f is None:
            continue
        values = f.value if isinstance(f.value, list) else [f.value]
        out += [(key, str(v)) for v in values]
    return out


def locate(rec: IncidentRecord, providers: Providers) -> list[LocationCandidate]:
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
