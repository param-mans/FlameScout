"""Brief synthesis (PRD FR-6, FR-9, FR-10). Deterministic baseline.

Considerations here are rule templates over dossier + incident facts, each with
evidence ids. This is also the PRD's "safe fallback" for when the LLM strategy
agent / critic loop is added: that agent may re-rank or re-word, but its output
must still pass critic.check_brief.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from flamescout_schemas import (Brief, BuildingDossier, Consideration, CrewFeedback, Evidence,
                                Fact, IncidentRecord)

from . import critic
from .tools import rules

BUILDING_KEYS = ["building.name", "building.use", "building.construction_status",
                 "building.stories", "building.height_est_m", "building.num_buildings",
                 "building.units", "building.year_built", "building.year_renovated",
                 "building.area_sqft", "building.construction", "building.sprinklered",
                 "building.sprinkler_type", "building.elevators", "building.walkup",
                 "building.occupant_load_est", "building.aerial_reach", "egress.inventory"]
INCIDENT_KEYS = ["incident.floor", "incident.visible", "people.reported", "people.trapped",
                 "alarm.sounding", "access.notes", "hazards.reported"]
EXPECTED_BUILDING = {"building.use": "building use", "building.stories": "stories",
                     "building.year_built": "year built", "building.construction": "construction type",
                     "building.sprinklered": "sprinklers"}
MARKER_GAP_LABELS = {"stair": "stairwells", "exit": "exits", "standpipe": "standpipe", "fdc": "FDC",
                     "key_box": "key box", "roof_access": "roof access"}
PRIORITY = {"life_safety": 0, "access": 1, "hazard": 2, "exposure": 3, "roof": 4, "staging": 5,
            "water_supply": 6}
MAX_CONSIDERATIONS = 5


def fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return " / ".join(fmt(x) for x in v)
    if isinstance(v, dict) and "label" in v:
        return str(v["label"])
    return str(v)


def build_brief(incident: IncidentRecord, dossier: BuildingDossier, version: int, cfg: dict,
                prev: Optional[Brief] = None, crew_notes: Iterable[CrewFeedback] = ()) -> Brief:
    evidence: dict[str, Evidence] = {**dossier.evidence, **incident.evidence}
    open_c = {c.key: c for c in incident.contradictions if not c.resolved_by_dispatcher}

    size_up: dict[str, Fact] = {k: dossier.facts[k] for k in BUILDING_KEYS if k in dossier.facts}
    for k in INCIDENT_KEYS:
        if k in incident.facts and k != "hazards.reported":
            f = incident.facts[k]
            if k in open_c:   # show every value callers gave, not just the first
                f = f.model_copy(update={"value": [v.value for v in open_c[k].values]})
            size_up[k] = f

    hazards: list[Fact] = []
    for fact, ev in rules.r04_hazards(dossier.facts.get("building.use"), dossier.tenants, cfg):
        hazards.append(fact)
        evidence[ev.id] = ev
    if "hazards.reported" in incident.facts:
        hazards.append(incident.facts["hazards.reported"])

    freshness = rules.r06_staleness(dossier.scan_captured_at, _costar_as_of(dossier), cfg)
    ev_stale = rules.rule_evidence("R-06", [e.id for e in dossier.evidence.values() if e.source == "costar"],
                                   {"scan_captured_at": str(dossier.scan_captured_at),
                                    **{k: freshness[k] for k in ("scan_band", "costar_band")}})
    evidence[ev_stale.id] = ev_stale

    considerations = _considerations(incident, dossier, size_up, hazards, freshness, ev_stale, open_c, cfg)

    brief = Brief(
        brief_id=f"{incident.incident_id}_v{version}", incident_id=incident.incident_id,
        building_id=dossier.building_id, version=version, location_confirmed=True,
        freshness=freshness, size_up=size_up, markers=dossier.markers, hazards=hazards,
        exposures=dossier.exposures, considerations=considerations,
        gaps=_gaps(incident, dossier, open_c, cfg), evidence=evidence, crew_notes=list(crew_notes))

    # Critic gate: drop any consideration that fails; if anything else fails, no considerations.
    problems = critic.check_brief(brief)
    bad = {p.target.split(":", 1)[1] for p in problems if p.target.startswith("consideration:")}
    if bad:
        brief.considerations = _rerank([c for c in brief.considerations if c.id not in bad])
    if any(not p.target.startswith("consideration:") for p in critic.check_brief(brief)):
        brief.considerations = []
    brief.changes_since_last = diff_briefs(prev, brief, incident) if prev else []
    return brief


def _costar_as_of(d: BuildingDossier):
    ev = next((e for e in d.evidence.values() if e.source == "costar"), None)
    return ev.captured_at.date() if ev and ev.captured_at else None


def _conf(facts: Iterable[Optional[Fact]], status: str, cfg: dict, scan: bool) -> float:
    c = rules.r09_confidence([f.confidence for f in facts if f is not None], status, cfg)  # type: ignore[arg-type]
    return min(c, cfg["no_scan_confidence_cap"]) if not scan else c


LAST = {"cn_stale_scan"}   # informational; only shown if a slot is free


def _rerank(cs: list[Consideration]) -> list[Consideration]:
    """Best item per category first (so one category can't crowd out the rest), then fill
    remaining slots by priority. Within a category, generation order = intended priority."""
    def key(c: Consideration) -> tuple[int, int]:
        return (c.id in LAST, PRIORITY[c.category])
    ordered = sorted(cs, key=key)   # stable: keeps generation order inside a category
    picked, seen = [], set()
    for c in ordered:
        if c.category not in seen and c.id not in LAST:
            picked.append(c)
            seen.add(c.category)
    picked += [c for c in ordered if c not in picked]
    picked = sorted(picked[:MAX_CONSIDERATIONS], key=key)
    return [c.model_copy(update={"rank": i + 1}) for i, c in enumerate(picked)]


def _side(text: str) -> set[str]:
    t = text.lower()
    sides = {s for s in ("north", "south", "east", "west") if s in t}
    if "rear" in t or "back" in t:
        sides.add("rear")
    if "front" in t or "lobby" in t:
        sides.add("front")
    return sides


def _considerations(inc: IncidentRecord, d: BuildingDossier, size_up: dict[str, Fact],
                    hazards: list[Fact], fresh: dict, ev_stale: Evidence,
                    open_c: dict, cfg: dict) -> list[Consideration]:
    out: list[Consideration] = []
    scan = d.scan_available
    floor_f = inc.facts.get("incident.floor")
    floor = floor_f.value if floor_f and "incident.floor" not in open_c else None

    def add(cid: str, cat: str, title: str, why: str, facts: list[Optional[Fact]],
            extra_ev: Iterable[str] = (), status: str = "inferred") -> None:
        ev = [e for f in facts if f for e in f.evidence_ids] + list(extra_ev)
        out.append(Consideration(id=cid, rank=0, category=cat, title=title, rationale=why,  # type: ignore[arg-type]
                                 confidence=_conf(facts, status, cfg, scan),
                                 evidence_ids=list(dict.fromkeys(ev))))

    # life safety
    if c := open_c.get("incident.floor"):
        vals = " and ".join(str(v.value) for v in c.values)
        calls = "; ".join(f"{v.call_id} said {v.value}" for v in c.values)
        add("cn_floor_conflict", "life_safety", f"Consider confirming the fire floor: callers reported {vals}",
            f"Callers disagree ({calls}). Not auto-resolved.", [inc.facts.get("incident.floor")],
            status="reported")
    trapped, reported = inc.facts.get("people.trapped"), inc.facts.get("people.reported")
    if (trapped and trapped.value is True) or (reported and reported.value is True):
        who = "trapped occupants" if trapped and trapped.value is True else "reported occupants"
        where = f" on and above floor {floor}" if floor else ""
        add("cn_search", "life_safety", f"Consider prioritizing search for {who}{where}",
            f"Caller(s) reported {who}." + (f" Fire floor reported as {floor}." if floor else ""),
            [trapped if trapped and trapped.value else reported, floor_f if floor else None], status="reported")

    # access / egress
    egress = d.facts.get("egress.inventory")
    stairs = [m for m in d.markers if m.kind == "stair"]
    if scan and egress and egress.value.get("flag"):
        add("cn_egress", "access", f"Consider verifying stairwell availability on arrival: {egress.value['flag'].split(' - ')[0].lower()}",
            "Rule R-05 counted stairwells in the scan.", [egress])
    elif scan and floor and stairs:
        on_floor = [m for m in stairs if m.floor == floor]
        if on_floor:
            labels = sorted({m.label for m in on_floor})
            add("cn_stairs_floor", "access", f"Consider {' or '.join(labels)} for access to floor {floor}",
                f"Scan shows {len(labels)} stairwell(s) serving floor {floor}.", [floor_f, egress],
                extra_ev=[e for m in on_floor for e in m.evidence_ids])
    notes = inc.facts.get("access.notes")
    for note in (notes.value if notes else []):
        if "stair" not in str(note).lower():
            continue
        match = [m for m in stairs if _side(str(note)) & _side(m.label)]
        seen = f" Scan shows {match[0].label}." if match else ""
        add("cn_caller_stairs", "access", f"Consider checking the {note} reported by a caller",
            f"Caller mentioned the {note} (possible occupants or smoke path).{seen}", [notes],
            extra_ev=[e for m in match[:1] for e in m.evidence_ids], status="reported")
        break

    # hazards
    for h in hazards[:2]:
        if h.key == "hazards.reported":
            add("cn_caller_hazard", "hazard", f"Consider caller-reported hazard: {fmt(h.value)}",
                "Reported by caller; not verified.", [h], status="reported")
        else:
            because = "; ".join(h.value.get("because", []))
            add(f"cn_{h.key.replace('.', '_')}", "hazard",
                f"Consider possible {h.value['label'][0].lower() + h.value['label'][1:]}",
                f"Occupancy heuristic (R-04) from {because}. Heuristic, verify on arrival.", [h])

    # exposure
    close = [x for x in d.exposures if x.get("band") == "close"]
    if close:
        x = close[0]
        out.append(Consideration(id="cn_exposure", rank=0, category="exposure",
                                 title=f"Consider exposure to {x['label']} (~{x['distance_m']:.0f} m)",
                                 rationale="Centroid distance from aerial imagery is inside the 'close' band (R-03).",
                                 confidence=min(x.get("confidence", 0.5), 1.0 if scan else cfg["no_scan_confidence_cap"]),
                                 evidence_ids=list(x["evidence_ids"])))

    # roof / reach
    reach = d.facts.get("building.aerial_reach")
    if reach and reach.value.get("reachable") == "no":
        add("cn_reach", "roof", "Consider that the estimated height may exceed configured aerial reach",
            f"R-01/R-02 margin {reach.value['margin_m']} m (estimate).", [reach])

    # data freshness
    if scan and fresh["scan_band"] in ("aging", "stale"):
        out.append(Consideration(id="cn_stale_scan", rank=0, category="access",
                                 title=f"Consider that the interior may have changed since the scan ({fresh['scan_age_days']} days old)",
                                 rationale=f"Scan age band: {fresh['scan_band']} (R-06).", confidence=0.5,
                                 evidence_ids=[ev_stale.id]))
    return _rerank(out)


def _gaps(inc: IncidentRecord, d: BuildingDossier, open_c: dict, cfg: dict) -> list[str]:
    gaps: list[str] = []
    missing = [label for k, label in EXPECTED_BUILDING.items() if k not in d.facts]
    if missing:
        gaps.append("Unknown: " + ", ".join(missing))
    if not d.scan_available:
        gaps.append("No interior scan on file: stairs, exits and interior layout unknown")
    else:
        kinds = {m.kind for m in d.markers}
        absent = [MARKER_GAP_LABELS[k] for k in cfg["expected_marker_kinds"] if k not in kinds]
        if absent:
            gaps.append("Not identified in scan: " + ", ".join(absent))
    reach = d.facts.get("building.aerial_reach")
    if reach and reach.value.get("reachable") == "unknown":
        gaps.append("Aerial reach: unknown (no apparatus config)")
    if not d.exposures:
        gaps.append("Exposures: no aerial data")
    for key, c in open_c.items():
        gaps.append(f"Unresolved: {key} ({' vs '.join(str(v.value) for v in c.values)})")
    not_reported = [k for k in ("incident.floor", "people.trapped") if k not in inc.facts]
    if not_reported:
        gaps.append("Not yet reported by callers: " + ", ".join(not_reported))
    return gaps


def _last_call(f: Fact, evidence: dict[str, Evidence]) -> str:
    for eid in reversed(f.evidence_ids):
        ev = evidence.get(eid)
        if ev and ev.source in ("call", "camera") and ev.locator:
            return f" ({ev.locator.get('call_id')})"
    return ""


def diff_briefs(prev: Brief, cur: Brief, inc: IncidentRecord) -> list[str]:
    out: list[str] = []
    for k, f in cur.size_up.items():
        old = prev.size_up.get(k)
        src = _last_call(f, cur.evidence)
        if old is None:
            out.append(f"+ {k}: {fmt(f.value)}{src}")
        elif old.value != f.value:
            mark = "!" if f.status == "contradicted" else "~"
            out.append(f"{mark} {k}: {fmt(old.value)} -> {fmt(f.value)}{src}")
        elif old.status != f.status:
            out.append(f"~ {k}: now {f.status}")
    out += [f"- {k}" for k in prev.size_up if k not in cur.size_up]
    old_h = {h.key: h for h in prev.hazards}
    for h in cur.hazards:
        if h.key not in old_h or old_h[h.key].value != h.value:
            out.append(f"+ hazard: {fmt(h.value)}")
    old_t = {c.title for c in prev.considerations}
    new_t = {c.title for c in cur.considerations}
    out += [f"+ {t}" for t in sorted(new_t - old_t)]
    out += [f"- {t}" for t in sorted(old_t - new_t)]
    if prev.building_id != cur.building_id:
        out.insert(0, f"~ building: {prev.building_id} -> {cur.building_id}")
    return out
