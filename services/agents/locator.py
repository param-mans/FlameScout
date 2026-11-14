"""Candidate buildings for an incident. Plain function, no LLM.

Scores every dossier fixture against what callers said about the location
(address and/or building name). The dispatcher picks from the result; the
orchestrator only passes it along. Live geocoding / CoStar search comes later.
"""
from __future__ import annotations

import re

from flamescout_schemas import BuildingCandidate, IncidentRecord

from . import dossier as dossier_mod

_SUFFIXES = {"blvd": "boulevard", "st": "street", "ave": "avenue", "rd": "road", "dr": "drive", "pl": "place"}


def _tokens(text: str) -> set[str]:
    return {_SUFFIXES.get(t, t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t != "the"}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def find_candidates(incident: IncidentRecord) -> list[BuildingCandidate]:
    """Best match first. Empty when callers gave no location or nothing matches."""
    said = [str(incident.facts[k].value) for k in ("location.address", "location.name") if k in incident.facts]
    out: list[BuildingCandidate] = []
    for path in sorted(dossier_mod.FIXTURES.glob("*/dossier.json")):
        d = dossier_mod.load_dossier(path.parent.name)
        name = str(d.facts["building.name"].value) if "building.name" in d.facts else d.building_id
        score = max((_similarity(s, known) for s in said for known in (d.address, name)), default=0.0)
        if score > 0:
            out.append(BuildingCandidate(building_id=d.building_id, name=name, address=d.address,
                                         score=round(score, 2)))
    return sorted(out, key=lambda c: (-c.score, c.building_id))
