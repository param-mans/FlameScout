"""IncidentService: the one place that wires fusion -> locator -> dossier -> brief.

All state is JSON in workspace/ (see store.py). Fusion engines are cached in
memory and rebuilt on cold start by replaying inputs.jsonl with the extraction
results recorded at ingest time (no second LLM call, same result).
Every state change is published on the EventBus for /ws/incidents/{id}.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from flamescout_schemas import (Brief, BuildingDossier, CallSegment, CopilotSuggestion,
                                CrewFeedback, IncidentRecord, LocationCandidate)

from services.agents import copilot, locator
from services.agents.rules_brief import build_brief
from services.agents.rules_dossier import build_dossier
from services.agents.extract import ExtractedFact, Extractor, get_extractor
from services.agents.fusion import FusionEngine
from services.agents.tools import rules
from services.agents.tools.providers import get_providers

from .config import Settings
from .events import EventBus
from .metrics import Metrics
from .store import NotFound, Workspace, check_id

log = logging.getLogger("flamescout.service")
LOCATION_KEYS = ("location.address", "location.name")


class Conflict(ValueError):
    pass


class RecordingExtractor:
    """Wraps the live extractor and remembers its last output for the replay log."""

    def __init__(self, inner: Extractor):
        self.inner = inner
        self.last: Optional[list[ExtractedFact]] = None

    def extract(self, text: str) -> list[ExtractedFact]:
        self.last = self.inner.extract(text)
        return self.last


class ReplayExtractor:
    def __init__(self) -> None:
        self.queue: list[list[ExtractedFact]] = []

    def extract(self, text: str) -> list[ExtractedFact]:
        return self.queue.pop(0) if self.queue else []


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


class IncidentService:
    def __init__(self, settings: Settings, bus: EventBus):
        self.settings = settings
        self.ws = Workspace(settings.workspace)
        self.bus = bus
        self.metrics = Metrics(self.ws, settings.tiger_url)
        self.cfg = rules.load_config()
        self._engines: dict[str, FusionEngine] = {}
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._guard = threading.Lock()
        self._traces: dict[str, str] = {}
        self.set_data_mode(settings.data_mode)

    # ---------------- plumbing ----------------
    def set_data_mode(self, mode: str) -> None:
        if mode not in ("live", "fixture"):
            raise ValueError("mode must be 'live' or 'fixture'")
        self.data_mode = mode
        self.providers = get_providers(mode, self.settings.fixtures, self.settings.env())

    @contextmanager
    def _lock(self, incident_id: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks[incident_id]
        with lock:
            yield

    def trace_id(self, incident_id: str) -> str:
        return self._traces.setdefault(incident_id, uuid.uuid4().hex)

    def _emit(self, incident_id: str, type_: str, payload: dict) -> None:
        self.bus.publish(incident_id, type_, payload, self.trace_id(incident_id))

    def _dir(self, incident_id: str):
        return self.ws.incident_dir(incident_id)

    def _save(self, rec: IncidentRecord) -> None:
        self.ws.write_json(self._dir(rec.incident_id) / "incident.json", rec)

    def _engine(self, incident_id: str) -> FusionEngine:
        check_id(incident_id)
        if incident_id in self._engines:
            return self._engines[incident_id]
        if not self.ws.incident_exists(incident_id):
            raise NotFound(incident_id)
        saved = self.ws.read_json(self._dir(incident_id) / "incident.json", IncidentRecord)
        replay = ReplayExtractor()
        eng = FusionEngine(incident_id, replay)
        for item in self.ws.read_jsonl(self._dir(incident_id) / "inputs.jsonl"):
            if item["type"] == "segment":
                if item.get("extracted") is not None:
                    replay.queue.append([ExtractedFact(**f) for f in item["extracted"]])
                eng.ingest(CallSegment(**item["segment"]))
            elif item["type"] == "resolve":
                eng.resolve(item["key"], item["value"])
        eng.extractor = RecordingExtractor(get_extractor())
        eng.record.status = saved.status
        eng.record.confirmed_building_id = saved.confirmed_building_id
        eng.record.created_at = saved.created_at
        for key, f in eng.record.facts.items():     # replay re-stamps updated_at; keep the original
            old = saved.facts.get(key)
            if old is not None and old.value == f.value:
                f.updated_at = old.updated_at
        self._engines[incident_id] = eng
        return eng

    # ---------------- incidents CRUD ----------------
    def create_incident(self, incident_id: Optional[str] = None) -> IncidentRecord:
        iid = check_id(incident_id) if incident_id else f"inc_{uuid.uuid4().hex[:10]}"
        with self._lock(iid):
            if self.ws.incident_exists(iid):
                raise Conflict(f"incident {iid} already exists")
            eng = FusionEngine(iid, RecordingExtractor(get_extractor()))
            self._engines[iid] = eng
            self._save(eng.record)
        self._emit(iid, "incident.updated", {"incident": eng.record.model_dump(mode="json")})
        return eng.record

    def list_incidents(self) -> list[IncidentRecord]:
        recs = [self.get_incident(i) for i in self.ws.incident_ids()]
        return sorted(recs, key=lambda r: r.created_at, reverse=True)

    def get_incident(self, incident_id: str) -> IncidentRecord:
        with self._lock(incident_id):
            return self._engine(incident_id).record

    def delete_incident(self, incident_id: str) -> None:
        with self._lock(incident_id):
            self.ws.delete_incident(check_id(incident_id))
            self._engines.pop(incident_id, None)
        self.bus.forget(incident_id)

    # ---------------- ingest (FR-1/FR-2) ----------------
    def ingest_segment(self, incident_id: str, seg: CallSegment) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self._lock(incident_id):
            eng = self._engine(incident_id)
            rec = eng.record
            loc_before = {k: rec.facts[k].value for k in LOCATION_KEYS if k in rec.facts}
            contra_before = {c.key: len(c.values) for c in rec.contradictions}
            open_before = {s.id for s in self.copilot(incident_id) if s.status == "open"}

            assert isinstance(eng.extractor, RecordingExtractor)
            eng.extractor.last = None
            changes = eng.ingest(seg)
            extracted = eng.extractor.last
            self.ws.append_jsonl(self._dir(incident_id) / "inputs.jsonl", {
                "type": "segment", "segment": seg.model_dump(),
                "extracted": [f.model_dump() for f in extracted] if extracted is not None else None})
            self._save(rec)
            fusion_ms = _ms(t0)

            self._emit(incident_id, "transcript.segment", {"segment": seg.model_dump(), "changes": changes})
            self._emit(incident_id, "incident.updated", {"incident": rec.model_dump(mode="json")})
            for c in rec.contradictions:
                if len(c.values) > contra_before.get(c.key, 0) and len(c.values) > 1:
                    self._emit(incident_id, "contradiction.raised", {"contradiction": c.model_dump(mode="json")})
            open_now = [s for s in self.copilot(incident_id) if s.status == "open"]
            if {s.id for s in open_now} != open_before:
                self._emit(incident_id, "copilot.suggestion",
                           {"suggestions": [s.model_dump() for s in open_now]})

            loc_after = {k: rec.facts[k].value for k in LOCATION_KEYS if k in rec.facts}
            candidates = None
            if loc_after != loc_before and not rec.confirmed_building_id:
                candidates = self._locate(incident_id, rec)
            brief = self._rebrief(incident_id, rec) if changes and rec.confirmed_building_id else None

        self.metrics.record(incident_id, "fusion", fusion_ms, changes=len(changes))
        self.metrics.log_segment(incident_id, seg)
        return {"changes": changes, "incident": rec, "fusion_ms": round(fusion_ms, 1),
                "candidates": candidates, "brief_version": brief.version if brief else None}

    # ---------------- locate + confirm (FR-3) ----------------
    def locate(self, incident_id: str) -> list[LocationCandidate]:
        with self._lock(incident_id):
            return self._locate(incident_id, self._engine(incident_id).record)

    def candidates(self, incident_id: str) -> list[LocationCandidate]:
        with self._lock(incident_id):
            self._engine(incident_id)
            p = self._dir(incident_id) / "candidates.json"
            return [LocationCandidate(**c) for c in self.ws.read_json(p)] if p.exists() else []

    def _locate(self, incident_id: str, rec: IncidentRecord) -> list[LocationCandidate]:
        t0 = time.perf_counter()
        cands = locator.locate(rec, self.providers)
        for c in cands:
            try:
                c.scan_available = bool(self.providers.costar.get_property(c.building_id).get("matterport_model_id"))
            except Exception:  # noqa: BLE001 - informational only
                c.scan_available = None
        self.ws.write_json(self._dir(incident_id) / "candidates.json", [c.model_dump() for c in cands])
        self.metrics.record(incident_id, "locate", _ms(t0), n=len(cands))
        self._emit(incident_id, "location.candidates", {"candidates": [c.model_dump() for c in cands]})
        return cands

    def confirm_location(self, incident_id: str, building_id: str) -> Brief:
        """Dispatcher's one-tap confirmation. The ONLY path that produces a brief for a new building."""
        check_id(building_id)
        t0 = time.perf_counter()
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            dossier, cached = self.dossier(building_id, incident_id=incident_id)   # raises NotFound first
            rec.confirmed_building_id = building_id
            rec.status = "briefing"
            self._save(rec)
            self._emit(incident_id, "incident.updated", {"incident": rec.model_dump(mode="json")})
            self.ws.write_json(self._dir(incident_id) / "dossier.json", dossier)
            brief = self._new_brief(incident_id, rec, dossier, force=True)
            rec.status = "confirmed" if brief.version == 1 else "updated"
            self._save(rec)
            self._emit(incident_id, "incident.updated", {"incident": rec.model_dump(mode="json")})
        ms = _ms(t0)
        row = self.metrics.record(incident_id, "time_to_brief", ms, cached_dossier=cached,
                                  building_id=building_id, version=brief.version)
        self._emit(incident_id, "metric.tick", row)
        return brief

    def resolve_contradiction(self, incident_id: str, key: str, value: Any) -> IncidentRecord:
        with self._lock(incident_id):
            eng = self._engine(incident_id)
            if not any(c.key == key for c in eng.record.contradictions):
                raise NotFound(f"no contradiction on {key}")
            eng.resolve(key, value)
            self.ws.append_jsonl(self._dir(incident_id) / "inputs.jsonl",
                                 {"type": "resolve", "key": key, "value": value})
            self._save(eng.record)
            self._emit(incident_id, "incident.updated", {"incident": eng.record.model_dump(mode="json")})
            self._emit(incident_id, "copilot.suggestion",
                       {"suggestions": [s.model_dump() for s in self.copilot(incident_id) if s.status == "open"]})
            if eng.record.confirmed_building_id:
                self._rebrief(incident_id, eng.record)
            return eng.record

    # ---------------- dossier + brief (FR-4, FR-6, FR-9) ----------------
    def dossier(self, building_id: str, refresh: bool = False,
                incident_id: Optional[str] = None) -> tuple[BuildingDossier, bool]:
        cache = self.ws.cache_dir("dossiers") / f"{check_id(building_id)}.json"
        if cache.exists() and not refresh:
            return self.ws.read_json(cache, BuildingDossier), True

        def progress(stage: str, data: dict) -> None:
            if incident_id:
                self._emit(incident_id, "dossier.progress", {"building_id": building_id, "stage": stage, **data})

        t0 = time.perf_counter()
        try:
            d = build_dossier(building_id, self.providers, self.cfg, self.ws.cache_dir("vision"), progress)
        except KeyError as e:
            raise NotFound(f"building {building_id}") from e
        self.metrics.record(incident_id or "-", "dossier", _ms(t0), building_id=building_id)
        self.ws.write_json(cache, d)
        return d, False

    def _rebrief(self, incident_id: str, rec: IncidentRecord) -> Optional[Brief]:
        p = self._dir(incident_id) / "dossier.json"
        if not p.exists():
            return None
        return self._new_brief(incident_id, rec, self.ws.read_json(p, BuildingDossier))

    def _new_brief(self, incident_id: str, rec: IncidentRecord, dossier: BuildingDossier,
                   force: bool = False) -> Brief:
        versions = self.ws.brief_versions(incident_id)
        prev = self.ws.read_json(self.ws.brief_path(incident_id, versions[-1]), Brief) if versions else None
        t0 = time.perf_counter()
        brief = build_brief(rec, dossier, (versions[-1] + 1) if versions else 1, self.cfg,
                            prev=prev, crew_notes=self._crew_notes(dossier.building_id))
        if prev and not force and not brief.changes_since_last:
            return prev                      # nothing visible changed: no version churn
        self.metrics.record(incident_id, "brief", _ms(t0), version=brief.version)
        self.ws.write_json(self.ws.brief_path(incident_id, brief.version), brief)
        self.metrics.log_brief(brief)
        if prev and rec.status in ("confirmed", "updated"):
            rec.status = "updated"
            self._save(rec)
        self._emit(incident_id, "brief.updated", {"version": brief.version,
                                                  "changes_since_last": brief.changes_since_last,
                                                  "brief": brief.model_dump(mode="json")})
        return brief

    def briefs(self, incident_id: str) -> list[Brief]:
        with self._lock(incident_id):
            self._engine(incident_id)
            return [self.ws.read_json(self.ws.brief_path(incident_id, v), Brief)
                    for v in self.ws.brief_versions(incident_id)]

    def brief(self, incident_id: str, version: Optional[int] = None) -> Brief:
        with self._lock(incident_id):
            self._engine(incident_id)
            versions = self.ws.brief_versions(incident_id)
            if not versions:
                raise NotFound(f"no brief yet for {incident_id} (awaiting location confirmation)")
            v = versions[-1] if version is None else version
            return self.ws.read_json(self.ws.brief_path(incident_id, v), Brief)

    def buildings(self) -> list[dict]:
        out = []
        for bid in self._fixture_ids():
            try:
                p = self.providers.costar.get_property(bid)
            except Exception:  # noqa: BLE001
                continue
            out.append({"building_id": bid, "name": p.get("name"), "address": p.get("address"),
                        "coordinates": {"lat": p.get("lat"), "lon": p.get("lon")},
                        "scan_available": bool(p.get("matterport_model_id"))})
        return out

    def _fixture_ids(self) -> list[str]:
        # Only this pipeline's fixtures ("_fixture" key); the orchestrator's live in the same folder.
        return sorted(p.parent.name for p in self.settings.fixtures.glob("*/costar.json")
                      if "_fixture" in json.loads(p.read_text()))

    # ---------------- copilot (FR-11) ----------------
    def _copilot_states(self, incident_id: str) -> dict[str, str]:
        p = self._dir(incident_id) / "copilot.json"
        return self.ws.read_json(p) if p.exists() else {}

    def copilot(self, incident_id: str) -> list[CopilotSuggestion]:
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            return copilot.suggestions(rec, self._copilot_states(incident_id))

    def set_suggestion(self, suggestion_id: str, status: str) -> CopilotSuggestion:
        incident_id, _ = copilot.split_suggestion_id(suggestion_id)
        with self._lock(check_id(incident_id)):
            match = next((s for s in self.copilot(incident_id) if s.id == suggestion_id), None)
            if match is None:
                raise NotFound(suggestion_id)
            states = self._copilot_states(incident_id)
            states[suggestion_id] = status
            self.ws.write_json(self._dir(incident_id) / "copilot.json", states)
            self._emit(incident_id, "copilot.suggestion",
                       {"suggestions": [s.model_dump() for s in self.copilot(incident_id) if s.status == "open"]})
            return match.model_copy(update={"status": status})

    # ---------------- feedback + audit (FR-14, FR-15) ----------------
    def add_feedback(self, incident_id: str, fact_key: str, verdict: str, note: Optional[str],
                     role: str) -> CrewFeedback:
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            fb = CrewFeedback(id=f"fb_{uuid.uuid4().hex[:10]}", incident_id=incident_id,
                              building_id=rec.confirmed_building_id, fact_key=fact_key,
                              verdict=verdict, note=note, role=role)  # type: ignore[arg-type]
            self.ws.append_jsonl(self._dir(incident_id) / "feedback.jsonl", fb)
            if fb.building_id:
                self.ws.append_jsonl(self.ws.building_dir(fb.building_id) / "feedback.jsonl", fb)
        self.audit(incident_id, role, "feedback", {"fact_key": fact_key, "verdict": verdict})
        return fb

    def feedback(self, incident_id: str) -> list[CrewFeedback]:
        with self._lock(incident_id):
            self._engine(incident_id)
            return [CrewFeedback(**r) for r in self.ws.read_jsonl(self._dir(incident_id) / "feedback.jsonl")]

    def _crew_notes(self, building_id: str) -> list[CrewFeedback]:
        rows = self.ws.read_jsonl(self.ws.building_dir(building_id) / "feedback.jsonl")
        return [CrewFeedback(**r) for r in rows[-20:]]

    def audit(self, incident_id: str, role: str, action: str, detail: Optional[dict] = None) -> None:
        if not self.ws.incident_exists(incident_id):
            return
        self.ws.append_jsonl(self._dir(incident_id) / "audit.jsonl", {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role,
            "action": action, "detail": detail or {}})

    def audit_log(self, incident_id: str) -> list[dict]:
        self._engine(incident_id)
        return self.ws.read_jsonl(self._dir(incident_id) / "audit.jsonl")
