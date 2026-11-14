"""Deterministic critic checks (PRD FR-6). The LLM critic, when added, runs AFTER these.

- every Fact / Marker / exposure / Consideration has evidence ids that resolve,
  or is explicitly status "unverified"
- considerations are advisory ("Consider ..."), no order language, <= 5, ranked 1..n
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from flamescout_schemas import Brief

ORDER_WORDS = re.compile(r"\b(must|immediately|do not|don't|order|you need to|required to|shall)\b", re.I)


@dataclass
class Problem:
    target: str     # "size_up:<key>", "hazard:<key>", "marker:<id>", "exposure:<id>", "consideration:<id>", "brief"
    message: str


def _check_ids(target: str, ids: list[str], status: str, evidence: dict, out: list[Problem]) -> None:
    if not ids:
        if status != "unverified":
            out.append(Problem(target, "no evidence and not marked unverified"))
        return
    missing = [i for i in ids if i not in evidence]
    if missing:
        out.append(Problem(target, f"unresolved evidence ids: {missing}"))


def check_brief(b: Brief) -> list[Problem]:
    out: list[Problem] = []
    for k, f in b.size_up.items():
        _check_ids(f"size_up:{k}", f.evidence_ids, f.status, b.evidence, out)
    for h in b.hazards:
        _check_ids(f"hazard:{h.key}", h.evidence_ids, h.status, b.evidence, out)
    for m in b.markers:
        _check_ids(f"marker:{m.id}", m.evidence_ids, m.status, b.evidence, out)
    for x in b.exposures:
        _check_ids(f"exposure:{x.get('id')}", x.get("evidence_ids", []), x.get("status", "inferred"),
                   b.evidence, out)
    if len(b.considerations) > 5:
        out.append(Problem("brief", f"{len(b.considerations)} considerations (max 5)"))
    if [c.rank for c in b.considerations] != list(range(1, len(b.considerations) + 1)):
        out.append(Problem("brief", "consideration ranks are not 1..n"))
    for c in b.considerations:
        t = f"consideration:{c.id}"
        _check_ids(t, c.evidence_ids, "inferred", b.evidence, out)
        if not c.title.startswith("Consider "):
            out.append(Problem(t, "title is not advisory ('Consider ...')"))
        if ORDER_WORDS.search(c.title) or ORDER_WORDS.search(c.rationale):
            out.append(Problem(t, "order language"))
    return out
