"""Brief assembly. Deterministic, no LLM.

The strategy agent authors only ConsiderationDraft (category, title, rationale,
evidence_ids). Everything else in a Brief - size-up facts, markers, hazards,
exposures, freshness, gaps, ids, ranks, confidences, the evidence dict,
changes_since_last - is copied or computed here, and a draft that is not
advisory or not backed by evidence that resolves is rejected before a Brief
is built.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from flamescout_schemas import (Brief, BuildingDossier, Consideration, Evidence, Fact,
                                IncidentRecord, now)

MAX_CONSIDERATIONS = 5
ADVISORY_PREFIX = "Consider "
UNSCORED_CONFIDENCE = 0.3  # evidence that no scored fact or marker cites


class ConsiderationDraft(BaseModel):
    category: Literal["access", "life_safety", "exposure", "hazard",
                      "roof", "staging", "water_supply"]
    title: str = Field(description='Advisory wording. Must start with "Consider ".')
    rationale: str = Field(description="One or two sentences, using only facts from the files.")
    evidence_ids: list[str] = Field(description="Evidence ids that support this, copied exactly.")


def merge_evidence(incident: IncidentRecord, dossier: BuildingDossier) -> dict[str, Evidence]:
    if clash := sorted(set(incident.evidence) & set(dossier.evidence)):
        raise ValueError(f"Evidence ids used by both incident and dossier: {clash}")
    return {**incident.evidence, **dossier.evidence}


def _draft_errors(d: ConsiderationDraft, evidence: dict[str, Evidence]) -> list[str]:
    errors: list[str] = []
    if not d.title.startswith(ADVISORY_PREFIX):
        errors.append(f'title must start with "{ADVISORY_PREFIX}" (advisory, not an order): {d.title!r}')
    if not d.rationale.strip():
        errors.append("rationale is empty")
    if not d.evidence_ids:
        errors.append("no evidence_ids; drop it or cite evidence")
    if unknown := [e for e in d.evidence_ids if e not in evidence]:
        errors.append(f"unknown evidence ids {unknown}")
    return errors


def check_considerations(drafts: list[ConsiderationDraft], evidence: dict[str, Evidence]) -> list[str]:
    """Why these drafts cannot go in a Brief. Empty list = acceptable."""
    errors: list[str] = []
    if len(drafts) > MAX_CONSIDERATIONS:
        errors.append(f"{len(drafts)} considerations; the maximum is {MAX_CONSIDERATIONS}")
    for i, d in enumerate(drafts, 1):
        errors += [f"#{i}: {e}" for e in _draft_errors(d, evidence)]
    return errors


def rejected_count(drafts: list[ConsiderationDraft], evidence: dict[str, Evidence]) -> int:
    """How many of these drafts the checks refuse: each faulty one, plus any beyond the maximum."""
    faulty = sum(1 for d in drafts if _draft_errors(d, evidence))
    return max(faulty, len(drafts) - MAX_CONSIDERATIONS)


def _evidence_confidence(incident: IncidentRecord, dossier: BuildingDossier) -> dict[str, float]:
    """Evidence id -> lowest confidence of anything that cites it."""
    out: dict[str, float] = {}
    scored = [(f.evidence_ids, f.confidence)
              for f in [*incident.facts.values(), *dossier.facts.values(), *dossier.tenants, *dossier.markers]]
    scored += [(x.get("evidence_ids", []), x["confidence"]) for x in dossier.exposures if "confidence" in x]
    for ids, conf in scored:
        for e in ids:
            out[e] = min(conf, out.get(e, 1.0))
    return out


def _gaps(incident: IncidentRecord, dossier: BuildingDossier) -> list[str]:
    gaps = [f"Callers disagree on {c.key}: {' vs '.join(str(v.value) for v in c.values)} (unresolved)"
            for c in incident.contradictions if not c.resolved_by_dispatcher]
    gaps += [f"Unverified: {f.key}" for f in [*dossier.facts.values(), *dossier.tenants]
             if f.status == "unverified"]
    if not dossier.scan_available:
        gaps.append("No interior scan available for this building")
    gaps += [f"Not yet asked or answered: {q}" for q in incident.open_questions]
    return gaps


def _freshness(dossier: BuildingDossier) -> dict:
    costar = [e.captured_at for e in dossier.evidence.values() if e.source == "costar" and e.captured_at]
    return {
        "scan_age_days": (now() - dossier.scan_captured_at).days if dossier.scan_captured_at else None,
        "costar_as_of": min(costar).date().isoformat() if costar else None,
    }


LABELS = {
    "location.address": "Address", "location.name": "Building name", "incident.floor": "Floor",
    "incident.visible": "Visible", "people.reported": "People reported", "people.trapped": "People trapped",
    "hazards.reported": "Reported hazards", "alarm.sounding": "Alarm", "access.notes": "Access notes",
}


def _show(value: Any) -> str:
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)


def _call_label(call_id: str) -> str:
    m = re.fullmatch(r"c(\d+)", call_id)
    return f"Call {m[1]}" if m else call_id


def _new_calls(fact: Fact, old: Optional[Fact], evidence: dict[str, Evidence]) -> str:
    """' (Call 2)': the calls behind evidence this fact gained since the previous brief."""
    seen = set(old.evidence_ids) if old else set()
    calls = [evidence[e].locator["call_id"] for e in fact.evidence_ids
             if e not in seen and e in evidence and evidence[e].source == "call" and evidence[e].locator]
    return f" ({', '.join(_call_label(c) for c in dict.fromkeys(calls))})" if calls else ""


def diff_briefs(prev: Brief, new: Brief, incident: IncidentRecord) -> list[str]:
    """What changed between two brief versions, in plain words. No LLM."""
    old = {**prev.size_up, **{f.key: f for f in prev.hazards}}
    cur = {**new.size_up, **{f.key: f for f in new.hazards}}
    contradictions = {c.key: c for c in incident.contradictions}
    out: list[str] = []
    for key, f in cur.items():
        label, o = LABELS.get(key, key), old.get(key)
        src = _new_calls(f, o, new.evidence)
        if o is None:
            out.append(f"New: {label} {_show(f.value)}{src}")
        elif f.status == "contradicted" and o.status != "contradicted":
            c = contradictions.get(key)
            values = " vs ".join(str(v.value) for v in c.values) if c else _show(f.value)
            out.append(f"{label} contradicted: {values}{src}")
        elif o.status == "contradicted" and f.status != "contradicted":
            out.append(f"{label} resolved: {_show(f.value)}")
        elif isinstance(f.value, list) and isinstance(o.value, list):
            if added := [v for v in f.value if v not in o.value]:
                out.append(f"{label} added: {_show(added)}{src}")
        elif f.value != o.value:
            out.append(f"{label} changed: {_show(o.value)} -> {_show(f.value)}{src}")
    out += [f"No longer reported: {LABELS.get(k, k)}" for k in old if k not in cur]
    if {c.title for c in prev.considerations} != {c.title for c in new.considerations}:
        out.append(f"Considerations redrafted: {len(new.considerations)} (was {len(prev.considerations)})")
    return out


def build_brief(incident: IncidentRecord, dossier: BuildingDossier,
                drafts: list[ConsiderationDraft], version: int = 1,
                extra_gaps: tuple[str, ...] = (), previous: Optional[Brief] = None,
                verifier_rejections: int = 0) -> Brief:
    evidence = merge_evidence(incident, dossier)
    if errors := check_considerations(drafts, evidence):
        raise ValueError("; ".join(errors))

    conf = _evidence_confidence(incident, dossier)
    considerations = [
        Consideration(id=f"c{i}", rank=i, category=d.category, title=d.title.strip(),
                      rationale=d.rationale.strip(), evidence_ids=list(dict.fromkeys(d.evidence_ids)),
                      confidence=min((conf[e] for e in d.evidence_ids if e in conf),
                                     default=UNSCORED_CONFIDENCE))
        for i, d in enumerate(drafts, 1)
    ]

    is_hazard = lambda k: k.startswith("hazard.") or k == "hazards.reported"  # noqa: E731
    facts: dict[str, Fact] = {**incident.facts, **dossier.facts, **{t.key: t for t in dossier.tenants}}
    brief = Brief(
        brief_id=f"{incident.incident_id}_v{version}",
        incident_id=incident.incident_id,
        version=version,
        location_confirmed=incident.confirmed_building_id == dossier.building_id,
        freshness=_freshness(dossier),
        size_up={k: f for k, f in facts.items() if not is_hazard(k)},
        markers=dossier.markers,
        hazards=[f for k, f in facts.items() if is_hazard(k)],
        exposures=dossier.exposures,
        considerations=considerations,
        gaps=[*extra_gaps, *_gaps(incident, dossier)],
        evidence=evidence,
        verifier_rejections=verifier_rejections,
    )
    if previous is not None:
        brief.changes_since_last = diff_briefs(previous, brief, incident)
    return brief
