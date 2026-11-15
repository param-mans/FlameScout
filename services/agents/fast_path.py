"""FAST_PIPELINE=1: the incident overlay and the brief's considerations from ONE structured model call.

docs/FAST_PIPELINE.md. No tools, no agent turns, thinking off. The model gets the transcript, the plan index
(size_rank, next_to, over, under, at_stairs), the listing facts and the scan / aerial digests, and returns what
the callers reported (each place as the caller's words in the plan's terms, never a room id) plus 2-4
considerations. Code does the rest, with the same rules as the agent path:

- every place goes through resolve_place.py: one matching room -> that room; several -> floor level (and a
  dispatcher question); none -> floor or unknown. The model never picks a room id;
- the overlay is assembled and checked with overlay.assemble / overlay.check (validate_overlay.py + the
  incident mapper's rules); the considerations with brief.check_considerations;
- anything wrong -> ONE retry that quotes the errors; wrong again -> FastPathFailed (the service falls back to
  the agent path). Nothing that fails a check is ever written.

The three data layers are pulled in code, concurrently (or copied from a fresh prefetch cache), like
orchestrator._ensure_dossier does. prepare() does the model work and needs no version; commit() numbers it,
writes overlay_vN.json + brief_vN.json and returns both, so the service can emit them in order.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from flamescout_schemas import Brief, BuildingDossier, IncidentOverlay, IncidentRecord

from . import cache, copilot
from . import dossier as dossier_mod
from . import memory as memory_mod
from . import overlay as overlay_mod
from .brief import (ConsiderationDraft, apply_memory, build_brief, check_considerations, low_confidence,
                    merge_evidence, observed_evidence, publish, wrong_markers)
from .critic import ORDER_WORDS
from .dossier import LAYERS, LayerData
from .overlay import (DraftFire, DraftHazard, DraftOccupant, OverlayDraft, PlaceQuery, Span, names_a_room,
                      place_label)
from .workspace import Workspace

log = logging.getLogger("flamescout.fast")

FAST_TIMEOUT_S = 25.0       # one model call; past it the attempt counts as failed
MAX_ATTEMPTS = 2            # the call, plus ONE retry that quotes the errors
MIN_CONSIDERATIONS, MAX_CONSIDERATIONS = 2, 4
NO_CRITIC_GAP = "Considerations were not checked by the critic"  # same gap as the agent path without a critique

FAST_PROMPT = """You map what 911 callers reported onto a building plan, for the fire crew's 3D viewer, and \
draft 2 to 4 advisory considerations for the first-arriving crew. Answer once, in the structured format.

PLACES. For every report give `place` in the caller's words, put in the plan index's terms (code resolves it
to a room; you never give room ids):
- floor_reported: the floor the caller gave (ground floor = 1; "upstairs" in a two-storey building is 2,
  "downstairs" is 1). Leave it out if they gave none and the room type alone does not tell.
- type: the plan index room type for the kind of room the caller named (a "study" is "office", a "lounge"
  "living room", "the kids' room" "bedroom"). Leave it out if the caller named no kind of room or the
  index has no such type.
- over / under / next_to: a room type the caller placed it against ("the bedroom over the kitchen" -> type
  "bedroom", over "kitchen"); at_stairs true for "by the stairs"; size "largest" / "smallest" for "the big /
  small one".
- Leave place out when nothing locates the report.

REPORTS (caller lines only; never anything the callers did not say):
- occupants: count (or count_range [min, max] when unsure; "someone" is 1). fire: kind smoke, fire or heat.
  hazards: kind gas, chemical, battery, electrical, structural or other (a gas stove or gas cans are "gas").
- each with: id (occ-1, fire-1, haz-1, ...), status "reported", confidence 0-1, evidence_ids (span ids of
  caller lines from the transcript, copied exactly), note (the caller's words, short).
- Callers disagree (different calls give different places or counts for the same people or fire): one entry
  per caller, each status "contradicted" with the same group_id, plus a contradictions entry {group_id, key
  (e.g. "occupants.location")}. Never pick a winner. One caller correcting themselves is not a contradiction.
- If a previous overlay is listed, keep the id of every report that is still the same report.
- objectives: "rescue" for reported occupants (target kind "occupant" with the occupant id, or
  "occupant_group" with a group_id), "recon" for fire and hazards (target kind "place" with the place);
  priority 1 first; one-sentence rationale; evidence_ids.

CONSIDERATIONS (2 to 4, most important first):
- title starts with "Consider " and is advisory. Never use: must, immediately, do not, don't, order, shall,
  required, guaranteed, 100%.
- rationale: one sentence, at most 25 words, only facts given below; say "reported" for what callers said.
- evidence_ids: copied exactly from the ids in brackets below (building data) or caller span ids; each one
  supports it. refs: ids of your reports, groups or objectives it is about.
- At least one consideration combines two building-data layers: its evidence_ids include ids from two of
  costar, matterport and vexcel (marked in the building data), where both really bear on the point (e.g. the
  fire floor from the listing and the interior stairs from the scan). Only ids of facts, markers or exposures.
- No interior scan: say nothing about the interior. Never say anything about windows. Never use a fact marked
  unverified."""


# ---------- what the model returns ----------

class FastPlace(BaseModel):
    floor_reported: Optional[int] = Field(None, description="Floor number the caller gave (ground floor = 1)")
    type: Optional[str] = Field(None, description="A room type from the plan index, e.g. bedroom, kitchen")
    size: Optional[Literal["largest", "smallest"]] = None
    over: Optional[str] = Field(None, description="Room type it is over")
    under: Optional[str] = Field(None, description="Room type it is under")
    next_to: Optional[str] = Field(None, description="Room type it is next to")
    at_stairs: Optional[bool] = None


class _FastReport(BaseModel):
    id: str
    status: Literal["reported", "unverified", "contradicted"] = "reported"
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    note: Optional[str] = None
    group_id: Optional[str] = None
    place: Optional[FastPlace] = None


class FastOccupant(_FastReport):
    count: Optional[int] = None
    count_range: Optional[list[int]] = None


class FastFire(_FastReport):
    kind: Literal["smoke", "fire", "heat"]


class FastHazard(_FastReport):
    kind: Literal["gas", "chemical", "battery", "electrical", "structural", "other"]


class FastContradiction(BaseModel):
    group_id: str
    key: str


class FastTarget(BaseModel):
    kind: Literal["occupant", "occupant_group", "place"]
    ref_id: Optional[str] = Field(None, description="Occupant id or group_id")
    place: Optional[FastPlace] = None


class FastObjective(BaseModel):
    id: str
    type: Literal["rescue", "recon"]
    priority: int = Field(ge=1)
    target: FastTarget
    rationale: str
    evidence_ids: list[str]


class FastConsideration(BaseModel):
    category: Literal["access", "life_safety", "exposure", "hazard", "roof", "staging", "water_supply"]
    title: str
    rationale: str
    evidence_ids: list[str]
    refs: list[str] = []


class FastOutput(BaseModel):
    occupants: list[FastOccupant] = []
    fire: list[FastFire] = []
    hazards: list[FastHazard] = []
    contradictions: list[FastContradiction] = []
    objectives: list[FastObjective] = []
    considerations: list[FastConsideration] = []


class FastPathFailed(RuntimeError):
    def __init__(self, errors: list[str], attempts: int, timings: dict):
        super().__init__("; ".join(errors)[:500])
        self.errors, self.attempts, self.timings = errors, attempts, timings


@dataclass
class Prepared:
    """A validated answer, not yet numbered (commit() gives it its version)."""
    building_id: str
    draft: OverlayDraft
    considerations: list[ConsiderationDraft]
    resolved: set[str]
    questions: list[dict]             # ambiguous places: dispatcher questions, written at commit
    index: dict
    spans: list[Span]
    dossier: BuildingDossier
    memory: list
    prefetched_at: Optional[datetime]
    model_id: str
    attempts: int
    timings: dict = field(default_factory=dict)


# ---------- model ----------

def overlay_model_name(override: Optional[str] = None) -> Optional[str]:
    """GEMINI_OVERLAY_MODEL (Gemini only), else the provider's usual fast model."""
    from .llm import model_name, provider

    if provider() == "gemini" and (override or os.getenv("GEMINI_OVERLAY_MODEL")):
        return override or os.getenv("GEMINI_OVERLAY_MODEL")
    return model_name("fast")


def available(model: Any = None, override: Optional[str] = None) -> bool:
    """A model to call: one passed in (tests), or the provider's key plus a model id."""
    from .llm import _env, provider

    if model is not None:
        return True
    return bool(os.getenv(_env("fast", provider())[0]) and overlay_model_name(override))


def make_model(override: Optional[str] = None) -> Any:
    from .llm import make_chat

    return make_chat("fast", timeout=FAST_TIMEOUT_S, max_retries=1, thinking="none",
                     model_id=overlay_model_name(override))


def _invoke(model: Any, messages: list) -> tuple[FastOutput, dict]:
    """One structured call (JSON schema, no tools). Returns the answer and its token usage."""
    out = model.with_structured_output(FastOutput, include_raw=True).invoke(messages)
    usage: dict = {}
    if isinstance(out, dict) and "parsed" in out:
        raw = out.get("raw")
        usage = dict(getattr(raw, "usage_metadata", None) or {})
        if out.get("parsing_error") is not None:
            raise ValueError(f"the answer did not match the schema: {str(out['parsing_error'])[:600]}")
        out = out["parsed"]
    if out is None:
        raise ValueError("the answer was empty")
    return (out if isinstance(out, FastOutput) else FastOutput.model_validate(out)), usage


# ---------- context ----------

def _load_layers(building_id: str, use_cache: bool = True) -> tuple[dict[str, LayerData], Optional[datetime], dict]:
    """The three layers, pulled concurrently (or a fresh prefetch cache). No interior scan: vexcel again with
    the exterior entries (orchestrator._ensure_exterior_entries)."""
    hit = cache.load_fresh(building_id) if use_cache else None
    if hit is not None:
        return dict(hit.layers), hit.prefetched_at, dict(hit.notes)
    with ThreadPoolExecutor(len(LAYERS), thread_name_prefix="fast-layer") as pool:
        futures = {name: pool.submit(dossier_mod.pull_layer, name, building_id) for name in LAYERS}
        layers = {name: f.result() for name, f in futures.items()}
    if not layers["matterport"].scan_available and not layers["vexcel"].exterior_entries:
        layers["vexcel"] = dossier_mod.pull_layer("vexcel", building_id, exterior_entries=True)
    return layers, None, {}


def _model_id(building_id: str) -> str:
    try:
        model = dossier_mod.get_providers().matterport.find_model({"property_id": building_id})
    except Exception:  # noqa: BLE001 - only a label for the viewer
        model = None
    return (model or {}).get("model_id") or building_id


def _plan_text(index: dict) -> str:
    if not overlay_mod.has_scan(index):
        return ("NO INTERIOR SCAN on file for this building: give places only a floor_reported (or nothing), "
                "never a room type.")
    rooms = index.get("rooms", {})
    floor_no = {f: n for n, f in enumerate(index.get("floors", []), 1)}
    kind = lambda rid: (rooms.get(rid) or {}).get("type") or rid  # noqa: E731
    lines = [f"Floors: {', '.join(str(n) for n in floor_no.values())} (ground floor = 1)",
             "Rooms (floor | type \"label\" | size rank among that type on its floor | relations):"]
    for rid, r in sorted(rooms.items(), key=lambda kv: (floor_no.get(kv[1].get("floor_id"), 0), kv[1].get("type") or "")):
        rel = [f"{name}: {', '.join(sorted(kind(x) for x in r.get(name) or []))}"
               for name in ("next_to", "over", "under") if r.get(name)]
        if r.get("at_stairs"):
            rel.append("at stairs")
        rank = f"{r.get('size_rank')}/{r.get('size_of')}" if r.get("size_rank") else "-"
        lines.append(f"- {floor_no.get(r.get('floor_id'), '?')} | {r.get('type')} \"{r.get('label', '')}\" | {rank}"
                     + (f" | {'; '.join(rel)}" if rel else ""))
    return "\n".join(lines)


def _context(incident: IncidentRecord, spans: list[Span], index: dict, layers: dict[str, LayerData],
             dossier: BuildingDossier, memory: list, previous: Optional[IncidentOverlay]) -> str:
    transcript = "\n".join(f"{s.span_id} [{s.speaker}] {s.text}" for s in spans)
    facts = "\n".join(f"- {f.key} = {f.value!r} ({f.status}) [{', '.join(f.evidence_ids)}]"
                      for f in incident.facts.values()) or "- none"
    parts = [f"TRANSCRIPT (cite caller lines by their span id):\n{transcript}",
             f"WHAT FUSION EXTRACTED FROM THE CALLS:\n{facts}",
             f"PLAN INDEX:\n{_plan_text(index)}",
             "BUILDING DATA (layer: costar = listing, matterport = interior scan, vexcel = aerial imagery):"]
    for name in LAYERS:
        if name in layers:
            parts.append(f"[{name}]\n{dossier_mod.digest(layers[name])}")
    if dossier.contradictions:
        parts.append("Data layers disagree:\n" + "\n".join(
            f"- {c.message} [{', '.join(e for v in c.values for e in v.evidence_ids)}]" for c in dossier.contradictions))
    if memory:
        wrong = {r.item_id for r in wrong_markers(memory)}
        parts.append("Crew memory (earlier incidents here; say \"a crew reported\"):\n" + "\n".join(
            f"- {r.sentence} [{r.evidence_id}]" + (" (marked WRONG: never mention it)" if r.item_id in wrong else "")
            for r in memory))
    if previous is not None:
        rows = [f"- {e.id}: {name} {overlay_mod.describe(e.location)} ({e.note or ''})"
                for name in ("occupants", "fire", "hazards") for e in getattr(previous, name)]
        parts.append(f"PREVIOUS OVERLAY (v{previous.version}), keep these ids for the same reports:\n" + "\n".join(rows))
    return "\n\n".join(parts)


# ---------- answer -> overlay draft ----------

def _floor_of(index: dict, room_ids: list[str]) -> Optional[int]:
    """The floor number shared by all these rooms, else None."""
    floors = index.get("floors", [])
    found = {(index.get("rooms", {}).get(r) or {}).get("floor_id") for r in room_ids}
    if len(found) != 1:
        return None
    floor_id = found.pop()
    return floors.index(floor_id) + 1 if floor_id in floors else None


def _locate(index: dict, place: Optional[FastPlace], about: str, evidence_ids: list[str],
            resolved: set[str], questions: list[dict]) -> dict:
    """The location the caller's words support, decided by resolve_place.py (overlay.resolve)."""
    if place is None:
        return {"kind": "unknown"}
    q = PlaceQuery(about=about, evidence_ids=evidence_ids, **place.model_dump())
    floor = q.floor_reported
    if names_a_room(q):
        result = overlay_mod.resolve(index, q)
        if result["status"] == "resolved":
            resolved.add(result["ids"][0])
            return {"kind": "room", "room_id": result["ids"][0]}
        if result["status"] == "ambiguous":
            questions.append({"query": q, "result": result})
            floor = floor or _floor_of(index, result["ids"])
    return {"kind": "floor", "floor_reported": floor} if floor else {"kind": "unknown"}


def _to_draft(out: FastOutput, index: dict) -> tuple[OverlayDraft, set[str], list[dict]]:
    resolved: set[str] = set()
    questions: list[dict] = []

    def entity(e: _FastReport, cls, **extra):
        about = f"{e.note or e.id} ({e.id})"
        data = e.model_dump(exclude={"place"}, exclude_none=True)
        data.update(extra, location=_locate(index, e.place, about, e.evidence_ids, resolved, questions))
        return cls(**data)

    objectives = []
    for o in out.objectives:
        t = o.target
        if t.kind in ("occupant", "occupant_group"):
            target = {"kind": t.kind, "ref_id": t.ref_id}
        else:
            loc = _locate(index, t.place, f"objective {o.id}", o.evidence_ids, resolved, questions)
            if loc["kind"] == "room":
                target = {"kind": "room", "ref_id": loc["room_id"]}
            elif loc["kind"] == "floor":
                target = {"kind": "floor", "floor_reported": loc["floor_reported"]}
            else:
                log.info("objective %s has no locatable target; left out", o.id)
                continue
        objectives.append({**o.model_dump(exclude={"target"}), "target": target})
    draft = OverlayDraft(
        occupants=[entity(e, DraftOccupant) for e in out.occupants],
        fire=[entity(e, DraftFire) for e in out.fire],
        hazards=[entity(e, DraftHazard) for e in out.hazards],
        contradictions=[{"group_id": c.group_id, "key": c.key, "resolved_by_dispatcher": False}
                        for c in out.contradictions],
        objectives=objectives)
    return draft, resolved, questions


def _drafts(out: FastOutput) -> list[ConsiderationDraft]:
    return [ConsiderationDraft(**c.model_dump()) for c in out.considerations]


def _evidence(incident: IncidentRecord, dossier: BuildingDossier, memory: list, spans: list[Span]) -> dict:
    callers = [s for s in spans if s.speaker == "caller"]
    return {**overlay_mod.span_evidence(callers), **merge_evidence(incident, dossier, memory)}


def _consideration_errors(drafts: list[ConsiderationDraft], incident: IncidentRecord, dossier: BuildingDossier,
                          memory: list, spans: list[Span], ids: set[str]) -> list[str]:
    errors = []
    if not MIN_CONSIDERATIONS <= len(drafts) <= MAX_CONSIDERATIONS:
        errors.append(f"{len(drafts)} considerations; give {MIN_CONSIDERATIONS} to {MAX_CONSIDERATIONS}")
    for i, d in enumerate(drafts, 1):
        if ORDER_WORDS.search(d.title) or ORDER_WORDS.search(d.rationale):
            errors.append(f"consideration #{i}: order language ({ORDER_WORDS.search(f'{d.title} {d.rationale}')[0]!r}); "
                          "keep it advisory")
    errors += [f"consideration {e}" for e in check_considerations(
        drafts, _evidence(incident, dossier, memory, spans), wrong_markers(memory), ids,
        observed_evidence(apply_memory(dossier, memory)))]
    return errors


def _check(out: FastOutput, incident: IncidentRecord, building_id: str, index: dict, spans: list[Span],
           dossier: BuildingDossier, memory: list, model_id: str):
    """(draft, considerations, resolved, questions, errors). errors empty = publishable."""
    try:
        draft, resolved, questions = _to_draft(out, index)
    except ValidationError as e:
        return None, [], set(), [], [f"overlay: {err['loc']}: {err['msg']}" for err in e.errors()][:10]
    try:
        ov = overlay_mod.assemble(draft, incident.incident_id, model_id, 1, index, spans)
    except (ValidationError, ValueError) as e:
        return None, [], set(), [], [f"overlay: {str(e)[:600]}"]
    errors = [f"overlay: {e}" for e in overlay_mod.check(ov, index, spans, resolved)]
    drafts = _drafts(out)
    errors += _consideration_errors(drafts, incident, dossier, memory, spans, overlay_mod.overlay_ids(ov))
    return draft, drafts, resolved, questions, errors


# ---------- public ----------

def prepare(incident: IncidentRecord, building_id: str, ws: Workspace, model: Any = None,
            model_override: Optional[str] = None, use_cache: bool = True) -> Prepared:
    """The model call (plus one retry with the errors) and every check. Raises FastPathFailed."""
    t0 = time.perf_counter()
    timings: dict[str, Any] = {"model_s": [], "tokens": []}
    with ThreadPoolExecutor(3, thread_name_prefix="fast-ctx") as pool:  # the layers load while the rest is read
        layers_f = pool.submit(_load_layers, building_id, use_cache)
        index_f = pool.submit(overlay_mod.load_plan_index, building_id)
        model_id_f = pool.submit(_model_id, building_id)
        spans = overlay_mod.transcript(ws)
        memory = memory_mod.building_memory(building_id)
        previous = _latest_overlay(ws)
        index, model_id = index_f.result(), model_id_f.result()
        layers, prefetched_at, _notes = layers_f.result()
    dossier = apply_memory(dossier_mod.merge_layers(building_id, layers.values()), memory)
    timings["context_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if not any(s.speaker == "caller" for s in spans):
        raise FastPathFailed(["no caller lines in the transcript yet"], 0, timings)

    from langchain_core.messages import HumanMessage, SystemMessage

    messages: list = [SystemMessage(FAST_PROMPT),
                      HumanMessage(_context(incident, spans, index, layers, dossier, memory, previous))]
    timings["prompt_chars"] = sum(len(m.content) for m in messages)
    model = model if model is not None else make_model(model_override)
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t_call = time.perf_counter()
        out: Optional[FastOutput] = None
        try:
            out, usage = _invoke(model, messages)
            timings["tokens"].append({k: usage.get(k) for k in ("input_tokens", "output_tokens") if k in usage})
        except Exception as e:  # noqa: BLE001 - schema, transport, quota: one retry, then the agent path
            errors = [f"the call failed: {type(e).__name__}: {str(e)[:400]}"]
        finally:
            timings["model_s"].append(round(time.perf_counter() - t_call, 2))
        if out is not None:
            t_check = time.perf_counter()
            draft, drafts, resolved, questions, errors = _check(out, incident, building_id, index, spans, dossier,
                                                                memory, model_id)
            timings["check_ms"] = round((time.perf_counter() - t_check) * 1000, 1)
            if not errors:
                timings["prepare_s"] = round(time.perf_counter() - t0, 2)
                log.info("fast path %s: valid answer on attempt %d (%s)", incident.incident_id, attempt, timings)
                return Prepared(building_id=building_id, draft=draft, considerations=drafts, resolved=resolved,
                                questions=questions, index=index, spans=spans, dossier=dossier, memory=memory,
                                prefetched_at=prefetched_at, model_id=model_id, attempts=attempt, timings=timings)
        log.warning("fast path %s: attempt %d rejected: %s", incident.incident_id, attempt, "; ".join(errors)[:800])
        if attempt < MAX_ATTEMPTS:
            previous_answer = out.model_dump_json(exclude_none=True) if out is not None else "(none)"
            messages = [*messages[:2], HumanMessage(
                "Your previous answer was rejected, nothing was published. Fix exactly these problems and return the "
                "full corrected answer:\n- " + "\n- ".join(errors) + f"\n\nYour previous answer:\n{previous_answer}")]
    timings["prepare_s"] = round(time.perf_counter() - t0, 2)
    raise FastPathFailed(errors, MAX_ATTEMPTS, timings)


def _latest_overlay(ws: Workspace) -> Optional[IncidentOverlay]:
    versions = sorted(int(m.group(1)) for rel in ws.list("overlay_v")
                      if (m := re.fullmatch(r"overlay_v(\d+)\.json", rel)))
    return overlay_mod.read(ws, versions[-1]) if versions else None


def commit(p: Prepared, incident: IncidentRecord, ws: Workspace, version: int) -> tuple[IncidentOverlay, Brief]:
    """Number the answer, check it once more, write overlay_vN.json and brief_vN.json (published). Raises
    ValueError when it no longer passes (nothing written)."""
    previous = overlay_mod.read(ws, version - 1)
    ov = overlay_mod.assemble(p.draft, incident.incident_id, p.model_id, version, p.index, p.spans, previous=previous)
    if errors := overlay_mod.check(ov, p.index, p.spans, p.resolved):
        raise ValueError("overlay: " + "; ".join(errors))
    prev_text = ws.read_or_none(f"brief_v{version - 1}.json")
    callers = [s for s in p.spans if s.speaker == "caller"]
    brief = build_brief(incident, p.dossier, p.considerations, version=version, extra_gaps=(NO_CRITIC_GAP,),
                        previous=Brief.model_validate_json(prev_text) if prev_text else None,
                        prefetched_at=p.prefetched_at, memory=p.memory,
                        extra_evidence=overlay_mod.span_evidence(callers), overlay_ids=overlay_mod.overlay_ids(ov))
    if low := [c.id for c in low_confidence(brief)]:  # nobody reviews them on this path: left out, said so
        brief.considerations = [c for c in brief.considerations if c.id not in low]
        for rank, c in enumerate(brief.considerations, 1):
            c.rank = rank
        brief.gaps.insert(0, f"Low-confidence consideration(s) {', '.join(low)} left out, not reviewed")
    brief = publish(brief)
    ws.write(overlay_mod.overlay_rel(version), overlay_mod.dump(ov))
    ws.write(f"brief_v{version}.json", brief.model_dump_json(indent=2))
    ws.write(f"places_v{version}.json", json.dumps(
        [{"query": q["query"].model_dump(), "result": q["result"]} for q in p.questions], indent=2))
    for q in p.questions:
        query = q["query"]
        copilot.write_place_question(ws, incident.incident_id, query.about, place_label(query),
                                     q["result"]["question"], q["result"]["ids"], query.evidence_ids)
    asked = {x.field for x in copilot.read_questions(ws)}
    if any(c.key not in asked for c in copilot.unresolved_life_safety(incident)):
        copilot.write_questions(ws, incident)
    return ov, brief
