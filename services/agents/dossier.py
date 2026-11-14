"""Building dossier loading.

For now every dossier is a hand-written fixture at
data/fixtures/<building_id>/dossier.json. The live CoStar / Matterport / Vexcel
adapters (PRD 7.4) are not built yet: DATA_MODE=live logs that and still reads
the fixture, so the demo never depends on API uptime.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from flamescout_schemas import BuildingDossier

log = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def unresolved_evidence(d: BuildingDossier) -> list[str]:
    """Evidence ids cited by a fact / tenant / marker / exposure but missing from d.evidence."""
    cited: list[str] = []
    for f in [*d.facts.values(), *d.tenants, *d.markers]:
        cited += f.evidence_ids
    for x in d.exposures:
        cited += x.get("evidence_ids", [])
    return sorted({e for e in cited if e not in d.evidence})


def load_dossier(building_id: str) -> BuildingDossier:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", building_id):
        raise ValueError(f"Bad building id {building_id!r}")
    if os.environ.get("DATA_MODE", "fixture") == "live":
        log.warning("DATA_MODE=live but live adapters are not built; using the fixture for %s", building_id)
    path = FIXTURES / building_id / "dossier.json"
    if not path.exists():
        raise FileNotFoundError(f"No dossier fixture for building {building_id!r} (expected {path})")
    d = BuildingDossier.model_validate_json(path.read_text())
    if d.building_id != building_id:
        raise ValueError(f"{path} is for {d.building_id!r}, not {building_id!r}")
    if missing := unresolved_evidence(d):
        raise ValueError(f"{path} cites evidence that is not in its evidence dict: {missing}")
    return d


def digest(d: BuildingDossier) -> str:
    """Compact text view for the dossier subagent. Every line keeps its evidence ids."""
    def ev(ids: list[str]) -> str:
        return f"[{', '.join(ids)}]" if ids else "[no evidence]"

    lines = [f"Building {d.building_id}: {d.address}",
             f"Interior scan: {'available, captured ' + str(d.scan_captured_at.date()) if d.scan_available and d.scan_captured_at else 'not available'}",
             "Facts:"]
    lines += [f"- {f.key} = {f.value!r} ({f.status}, conf {f.confidence}) {ev(f.evidence_ids)}"
              for f in [*d.facts.values(), *d.tenants]]
    lines.append("Markers:")
    lines += [f"- {m.id} {m.kind}: {m.label} ({m.status}, conf {m.confidence}) {ev(m.evidence_ids)}"
              for m in d.markers]
    lines.append("Exposures:")
    lines += [f"- {x.get('label')} at {x.get('distance_m')} m ({x.get('status')}) {ev(x.get('evidence_ids', []))}"
              for x in d.exposures]
    return "\n".join(lines)
