"""IncidentService: the one place that wires fusion -> locator -> brief.

Two pipelines write briefs (Settings.pipeline / PIPELINE):
  orchestrator (default)  the deep agent in services/agents/orchestrator.py (docs/agents-api.md):
                          start_incident when the orchestrator's locator finds candidates,
                          resume_incident on confirm, update_incident on new facts, review_brief
                          for a held brief.
  rules                   rules_dossier + rules_brief. Used for an incident when the confirmed
                          building is not one of the orchestrator's candidates, or when an
                          orchestrator call raises. Once an incident falls back, it stays on rules.

Storage: the API's own files (replay log, audit, copilot states, metrics) are JSON under
<workspace>/_api/ (store.py). The incident's documents the agent and memory.feedback() read
(incident.json, candidates, brief_vN.json, todos.json, ...) live in the orchestrator workspace
(services/agents/workspace.py: MongoDB when MONGODB_URI is set, else <workspace>/<incident_id>/).
Briefs from both pipelines are written there, so there is one brief store.

Brief work runs on one background worker per incident (confirm 35-40 s, update ~30 s on the
orchestrator), so calls for one incident never overlap and requests return at once; results go
out as WebSocket events. Fusion engines are cached in memory and rebuilt on cold start by
replaying inputs.jsonl with the extraction results recorded at ingest time.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Sequence

from flamescout_schemas import (Brief, BuildingDossier, CallSegment, CopilotSuggestion,
                                CrewFeedback, IncidentRecord, LocationCandidate, Progress)

from services.agents import copilot, fast_path, locator, memory, mongo_store, orchestrator
from services.agents.extract import ExtractedFact, Extractor, get_extractor
from services.agents.fusion import FusionEngine
from services.agents.rules_brief import build_brief
from services.agents.rules_dossier import build_dossier
from services.agents.tools import rules
from services.agents.tools.providers import get_providers
from services.agents.workspace import Workspace as AgentWorkspace
from services.agents.workspace import incident_workspace

from .config import Settings
from .events import EventBus
from .metrics import Metrics
from .store import NotFound, Workspace, check_id

log = logging.getLogger("flamescout.service")
LOCATION_KEYS = ("location.address", "location.name")
PIPELINES = ("orchestrator", "rules")
AUTO_CONFIRM_MIN_SCORE = 0.8    # AUTO_CONFIRM_LOCATION: the best candidate's score ...
AUTO_CONFIRM_MIN_MARGIN = 0.2   # ... and its lead over the second (a single candidate always qualifies)
FAST_DEBOUNCE_S = 1.0           # FAST_PIPELINE: a change after confirmation runs the fast path this long after the last one
FAST_RUNS_PER_CALL = 2          # ... at most this many times per call (mid-call, at the end)
FAST_TODOS = ("Map reports onto the plan", "CoStar property record", "Matterport interior scan",
              "Vexcel aerial imagery", "Draft considerations", "Brief ready")


@dataclass(eq=False)
class FastState:
    """FAST_PIPELINE for one confirmed building of one incident (docs/FAST_PIPELINE.md)."""
    building_id: str
    t0: float                                  # perf_counter() at the confirmation, for time_to_brief
    started: int = 0                           # runs started (each run's generation number)
    emitted: int = 0                           # generation of the newest run that was emitted (last one wins)
    runs: dict = field(default_factory=lambda: defaultdict(int))  # call_id -> runs started for that call
    timer: Optional[threading.Timer] = None    # the debounce of the next run
    pending_call: Optional[str] = None
    futures: list = field(default_factory=list)
    failed: bool = False                       # fell back to the agent path: that path owns the incident now


def auto_confirm_pick(cands: Sequence[LocationCandidate]) -> Optional[str]:
    """The building to confirm without the dispatcher, or None when the choice is not clear."""
    if not cands:
        return None
    ranked = sorted(cands, key=lambda c: -c.score)
    if len(ranked) == 1:
        return ranked[0].building_id
    best, second = ranked[0], ranked[1]
    if best.score >= AUTO_CONFIRM_MIN_SCORE and best.score - second.score >= AUTO_CONFIRM_MIN_MARGIN - 1e-9:
        return best.building_id
    return None


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
        if settings.pipeline not in PIPELINES:
            raise ValueError(f"PIPELINE must be one of {PIPELINES}, not {settings.pipeline!r}")
        self.settings = settings
        self.ws = Workspace(settings.workspace / "_api")
        # None = MongoDB; a path = local files (incident_workspace's convention)
        self.agents_root = None if mongo_store.enabled() else settings.workspace
        self.bus = bus
        self.metrics = Metrics(self.ws, settings.tiger_url)
        self.cfg = rules.load_config()
        self._engines: dict[str, FusionEngine] = {}
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._guard = threading.Lock()
        self._traces: dict[str, str] = {}
        self._jobs: dict[str, ThreadPoolExecutor] = {}
        self._notes: dict[str, list[str]] = defaultdict(list)   # change notes waiting for update_incident
        self._fast: dict[str, FastState] = {}                    # FAST_PIPELINE state per incident
        self._fast_pool = ThreadPoolExecutor(8, thread_name_prefix="fast")
        self.fast_model: Any = None   # tests put a fake chat model here; None = fast_path.make_model()
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

    def confirmed_by(self, incident_id: str) -> Optional[str]:
        """"auto" (AUTO_CONFIRM_LOCATION), "dispatcher" (POST /confirm-location or a simulated tap), or None."""
        p = self._dir(incident_id) / "confirmation.json"
        return self.ws.read_json(p).get("confirmed_by") if p.exists() else None

    def incident_json(self, rec: IncidentRecord) -> dict:
        """The incident as the API and incident.updated send it: the record plus confirmed_by."""
        return {**rec.model_dump(mode="json"), "confirmed_by": self.confirmed_by(rec.incident_id)}

    def _emit_incident(self, rec: IncidentRecord) -> None:
        self._emit(rec.incident_id, "incident.updated", {"incident": self.incident_json(rec)})

    def _agents_ws(self, incident_id: str) -> AgentWorkspace:
        return incident_workspace(incident_id, self.agents_root)

    def _save(self, rec: IncidentRecord, segments: Sequence[CallSegment] = ()) -> None:
        self.ws.write_json(self._dir(rec.incident_id) / "incident.json", rec)
        try:   # the orchestrator's and memory's copy (+ Tiger facts/segments)
            orchestrator.save_incident(rec, segments, self.agents_root)
        except Exception:  # noqa: BLE001 - the API's copy is saved; the brief path logs its own failure
            log.exception("could not save %s to the orchestrator workspace", rec.incident_id)

    # ---------------- background jobs (one serial worker per incident) ----------------
    def _submit(self, incident_id: str, fn: Callable[..., Any], *args: Any) -> Future:
        with self._guard:
            ex = self._jobs.get(incident_id)
            if ex is None:
                ex = self._jobs[incident_id] = ThreadPoolExecutor(1, thread_name_prefix=f"brief-{incident_id}")

        def run() -> Any:
            try:
                return fn(*args)
            except Exception:
                log.exception("brief job %s for %s failed", getattr(fn, "__name__", fn), incident_id)
                raise
        return ex.submit(run)

    def wait(self, incident_id: str, timeout: Optional[float] = None) -> None:
        """Block until every brief job queued so far for the incident has finished (fast-path runs, a pending
        debounce, and the incident's worker)."""
        while True:
            with self._guard:
                st = self._fast.get(incident_id)
                timer, futures = (st.timer, list(st.futures)) if st else (None, [])
            if timer is not None:
                timer.join(timeout)
            for f in futures:
                try:
                    f.result(timeout)
                except Exception:  # noqa: BLE001 - logged by the run itself
                    pass
            self._submit(incident_id, lambda: None).result(timeout)
            with self._guard:
                st = self._fast.get(incident_id)
                busy = st is not None and ((st.timer is not None and st.timer.is_alive())
                                           or any(not f.done() for f in st.futures))
            if not busy:
                return

    def shutdown(self) -> None:
        with self._guard:
            jobs, self._jobs = list(self._jobs.values()), {}
            fast, self._fast = list(self._fast.values()), {}
        for st in fast:
            if st.timer is not None:
                st.timer.cancel()
        self._fast_pool.shutdown(wait=False, cancel_futures=True)
        for ex in jobs:
            ex.shutdown(wait=False, cancel_futures=True)

    # ---------------- pipeline per incident ----------------
    def pipeline(self, incident_id: str) -> str:
        p = self._dir(incident_id) / "pipeline.json"
        return self.ws.read_json(p)["pipeline"] if p.exists() else self.settings.pipeline

    def _set_pipeline(self, incident_id: str, name: str, reason: Optional[str] = None) -> None:
        self.ws.write_json(self._dir(incident_id) / "pipeline.json", {"pipeline": name, "reason": reason})

    def _fall_back(self, incident_id: str, reason: str) -> None:
        log.warning("incident %s falls back to the rules pipeline: %s", incident_id, reason)
        self._set_pipeline(incident_id, "rules", reason)

    def _on_todos(self, incident_id: str) -> Callable[[Progress], None]:
        def push(p: Progress) -> None:
            self._emit(incident_id, "dossier.progress", p.model_dump(mode="json"))
        return push

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
            self._agents_ws(iid).clear()   # an id seen before: no stale briefs from an earlier run
            self._save(eng.record)
        self._emit_incident(eng.record)
        return eng.record

    def list_incidents(self) -> list[IncidentRecord]:
        recs = [self.get_incident(i) for i in self.ws.incident_ids()]
        return sorted(recs, key=lambda r: r.created_at, reverse=True)

    def get_incident(self, incident_id: str) -> IncidentRecord:
        with self._lock(incident_id):
            return self._engine(incident_id).record

    def transcript(self, incident_id: str) -> list[dict[str, Any]]:
        """Every segment so far as transcript.segment payloads ({segment, extracted}), in order. Lets a
        client that joins or reconnects mid-call (another device following a Call in) catch up."""
        with self._lock(incident_id):
            self._engine(incident_id)
            out = []
            for item in self.ws.read_jsonl(self._dir(incident_id) / "inputs.jsonl"):
                if item.get("type") != "segment":
                    continue
                ex = item.get("extracted") or []
                out.append({"segment": item["segment"],
                            "extracted": [{"key": f["key"], "value": f["value"], "quote": f["quote"]} for f in ex]})
            return out

    def delete_incident(self, incident_id: str) -> None:
        with self._lock(incident_id):
            self.ws.delete_incident(check_id(incident_id))
            self._engines.pop(incident_id, None)
            self._notes.pop(incident_id, None)
            self._drop_fast(incident_id)
            try:
                self._agents_ws(incident_id).clear()
            except Exception:  # noqa: BLE001
                log.exception("could not clear the orchestrator workspace of %s", incident_id)
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
            self._save(rec, [seg])
            fusion_ms = _ms(t0)

            # What the extractor understood from this line (caller lines only), so the UI can show how it was received.
            extracted_out = [{"key": f.key, "value": f.value, "quote": f.quote} for f in extracted] if extracted else []
            self._emit(incident_id, "transcript.segment",
                       {"segment": seg.model_dump(), "changes": changes, "extracted": extracted_out})
            self._emit_incident(rec)
            for c in rec.contradictions:
                if len(c.values) > contra_before.get(c.key, 0) and len(c.values) > 1:
                    self._emit(incident_id, "contradiction.raised", {"contradiction": c.model_dump(mode="json")})
            open_now = [s for s in self.copilot(incident_id) if s.status == "open"]
            if {s.id for s in open_now} != open_before:
                self._emit(incident_id, "copilot.suggestion",
                           {"suggestions": [s.model_dump() for s in open_now]})

            loc_after = {k: rec.facts[k].value for k in LOCATION_KEYS if k in rec.facts}
            candidates = None
            confirmed_before = rec.confirmed_building_id   # an auto-confirm here already reads these changes
            if loc_after != loc_before and not confirmed_before:
                candidates = self._locate(incident_id, rec)
                open_after = [s for s in self.copilot(incident_id) if s.status == "open"]
                if {s.id for s in open_after} != {s.id for s in open_now}:   # e.g. "Address not in catalog"
                    self._emit(incident_id, "copilot.suggestion",
                               {"suggestions": [s.model_dump() for s in open_after]})
                self._maybe_auto_confirm(incident_id, candidates)
            if changes and confirmed_before:
                self._queue_update(incident_id, changes, seg.call_id)

        self.metrics.record(incident_id, "fusion", fusion_ms, changes=len(changes))
        self.metrics.log_segment(incident_id, seg)
        return {"changes": changes, "incident": rec, "fusion_ms": round(fusion_ms, 1),
                "candidates": candidates, "extracted": extracted_out}

    def _queue_update(self, incident_id: str, notes: list[str], call_id: Optional[str] = None) -> None:
        """New facts after confirmation: the next brief version, on the incident's worker (or the fast path)."""
        if self._fast_schedule(incident_id, call_id):
            return
        if self.pipeline(incident_id) == "orchestrator":
            self._notes[incident_id].extend(notes)
            self._submit(incident_id, self._drain_updates, incident_id)
        else:
            self._submit(incident_id, self._rules_update, incident_id)

    # ---------------- locate + confirm (FR-3) ----------------
    def locate(self, incident_id: str) -> list[LocationCandidate]:
        with self._lock(incident_id):
            return self._locate(incident_id, self._engine(incident_id).record)

    def candidates(self, incident_id: str) -> list[LocationCandidate]:
        with self._lock(incident_id):
            self._engine(incident_id)
            p = self._dir(incident_id) / "candidates.json"
            return [LocationCandidate(**c) for c in self.ws.read_json(p)] if p.exists() else []

    def _orchestrator_candidates(self, rec: IncidentRecord) -> list[LocationCandidate]:
        if self.settings.pipeline != "orchestrator":
            return []
        try:
            return list(locator.find_candidates(rec))
        except Exception:  # noqa: BLE001 - the rules locator still answers
            log.exception("orchestrator locator failed for %s", rec.incident_id)
            return []

    def _locate(self, incident_id: str, rec: IncidentRecord) -> list[LocationCandidate]:
        """Orchestrator path: its own locator, so the building the dispatcher taps is one the agent
        accepts, then start_incident in the background. No orchestrator candidates: rules locator."""
        t0 = time.perf_counter()
        cands = self._orchestrator_candidates(rec)
        if cands:
            if not self.fast_enabled():   # the fast path needs no agent waiting at the confirmation
                self._submit(incident_id, self._start_job, incident_id)
        else:
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

    def _start_job(self, incident_id: str) -> None:
        try:
            orchestrator.start_incident(incident_id, self.agents_root, on_todos=self._on_todos(incident_id))
        except ValueError as e:   # e.g. the location changed and matches nothing now
            log.warning("start_incident %s: %s", incident_id, e)

    def _maybe_auto_confirm(self, incident_id: str, cands: Sequence[LocationCandidate]) -> None:
        """AUTO_CONFIRM_LOCATION=1 and a clear candidate: confirm it through confirm_location (the
        endpoint's path), so the events are the same as a dispatcher's tap; confirmed_by = "auto"."""
        if not self.settings.auto_confirm_location:
            return
        building_id = auto_confirm_pick(cands)
        if building_id is None:
            return   # ambiguous: wait for the dispatcher
        try:
            self.confirm_location(incident_id, building_id, confirmed_by="auto")
        except (NotFound, ValueError) as e:   # e.g. the building vanished: the dispatcher still can
            log.warning("auto-confirm of %s for %s failed: %s", building_id, incident_id, e)
            return
        self.audit(incident_id, "system", "confirm_location", {"building_id": building_id, "confirmed_by": "auto"})

    def confirm_location(self, incident_id: str, building_id: Optional[str],
                         confirmed_by: str = "dispatcher") -> dict[str, Any]:
        """The ONLY path that produces a brief for a new building: the dispatcher's one-tap confirmation
        (confirmed_by "dispatcher") or AUTO_CONFIRM_LOCATION (confirmed_by "auto").
        Returns at once; the brief arrives as brief.updated. building_id None = "none of these"."""
        t0 = time.perf_counter()
        with self._lock(incident_id):
            eng = self._engine(incident_id)
            if building_id is None:
                self._submit(incident_id, self._reject_job, incident_id)
                return {"incident_id": incident_id, "status": "rejected"}
            check_id(building_id)
            if building_id in [c.building_id for c in self._orchestrator_candidates(eng.record)]:
                pipeline = "orchestrator"
            else:
                pipeline = "rules"
                try:
                    self.providers.costar.get_property(building_id)
                except KeyError as e:
                    raise NotFound(f"building {building_id}") from e
            self._set_pipeline(incident_id, pipeline)
            self._notes.pop(incident_id, None)   # notes so far are in the record the new brief reads
            self._drop_fast(incident_id)         # a re-confirmation: runs for the earlier building are void
            fast = pipeline == "orchestrator" and self.fast_enabled()
            eng.confirm(building_id)
            eng.record.status = "briefing"
            self.ws.write_json(self._dir(incident_id) / "confirmation.json",
                               {"building_id": building_id, "confirmed_by": confirmed_by})
            self._save(eng.record)
            self._emit_incident(eng.record)
            call_id = eng.record.call_ids[-1] if eng.record.call_ids else None
        if fast:
            with self._guard:
                self._fast[incident_id] = FastState(building_id, t0)
            self._fast_start(incident_id, call_id)   # at once: the first run is the mid-call one
        else:
            self._submit(incident_id, self._confirm_job, incident_id, building_id, pipeline, t0)
        return {"incident_id": incident_id, "building_id": building_id, "status": "briefing",
                "pipeline": pipeline, "confirmed_by": confirmed_by}

    def _reject_job(self, incident_id: str) -> None:
        try:
            orchestrator.resume_incident(incident_id, None)   # on_todos publishes status "rejected"
        except Exception as e:  # noqa: BLE001 - no session / not paused: say it ourselves
            log.info("reject %s without an orchestrator session: %s", incident_id, e)
            self._emit(incident_id, "dossier.progress", {"incident_id": incident_id, "status": "rejected",
                                                         "version": 0, "todos": [], "candidates": []})

    def _confirm_job(self, incident_id: str, building_id: str, pipeline: str, t0: float) -> None:
        if pipeline == "orchestrator":
            try:
                r = self._resume(incident_id, building_id)
            except Exception as e:  # noqa: BLE001 - the crew still gets a brief, from the rules pipeline
                log.exception("orchestrator confirm failed for %s", incident_id)
                self._fall_back(incident_id, f"orchestrator confirm failed: {type(e).__name__}: {e}")
            else:
                self._publish_run(incident_id, r, t0)
                self._drain_updates(incident_id)
                return
        self._rules_confirm(incident_id, building_id, t0)

    def _resume(self, incident_id: str, building_id: str) -> orchestrator.RunResult:
        try:
            return orchestrator.resume_incident(incident_id, building_id)
        except ValueError as e:
            # No session (restart without Tiger, start never ran), already confirmed (re-confirm),
            # or the candidates moved on: start the thread again, then confirm.
            log.info("resume %s: %s; starting the incident again", incident_id, e)
            orchestrator.start_incident(incident_id, self.agents_root, on_todos=self._on_todos(incident_id))
            return orchestrator.resume_incident(incident_id, building_id)

    def _publish_run(self, incident_id: str, r: orchestrator.RunResult, t0: float) -> None:
        brief = r.brief
        if brief is None:
            return
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            rec.status = "confirmed" if brief.version == 1 else "updated"
            self._save(rec)
        if brief.published:
            self.metrics.log_brief(brief)
        self._emit(incident_id, "brief.updated", self._brief_event(
            brief, "orchestrator", r.status, r.needs_review, r.fallback_reason, r.trace_run_id))
        self._emit_overlay(incident_id, brief.version)
        self._emit_incident(rec)
        if brief.version == 1:
            row = self.metrics.record(incident_id, "time_to_brief", _ms(t0), cached_dossier=bool(
                brief.freshness.get("prefetched")), building_id=brief.building_id, version=1,
                pipeline="orchestrator")
            self._emit(incident_id, "metric.tick", row)

    @staticmethod
    def _brief_event(brief: Brief, pipeline: str, status: str, needs_review: Sequence[str] = (),
                     fallback_reason: Optional[str] = None, trace_run_id: Optional[str] = None) -> dict:
        return {"version": brief.version, "status": status, "published": brief.published,
                "needs_review": list(needs_review), "fallback_reason": fallback_reason,
                "pipeline": pipeline, "trace_run_id": trace_run_id,
                "changes_since_last": brief.changes_since_last, "brief": brief.model_dump(mode="json")}

    def _emit_overlay(self, incident_id: str, version: int) -> None:
        """overlay.updated right after brief.updated (docs/INCIDENT_OVERLAY_SPEC.md, Transport).
        The producer writes overlay_vN.json to the incident workspace and validates it against the plan
        index; this only relays it. No file for this version, or one that is not this incident's
        version N: nothing is sent, so the viewer keeps the previous overlay."""
        overlay = self._read_overlay(incident_id, version)
        if overlay is not None:
            self._emit(incident_id, "overlay.updated", {"version": version, "overlay": overlay})

    def _read_overlay(self, incident_id: str, version: int) -> Optional[dict]:
        """overlay_vN.json when it is this incident's version N, else None (logged)."""
        text = self._agents_ws(incident_id).read_or_none(f"overlay_v{version}.json")
        if text is None:
            log.info("no overlay_v%d.json for %s: overlay.updated not sent", version, incident_id)
            return None
        try:
            overlay = json.loads(text)
        except json.JSONDecodeError as e:
            log.warning("overlay_v%d.json of %s is not JSON (%s): not sent", version, incident_id, e)
            return None
        meta = overlay if isinstance(overlay, dict) else {}
        if (meta.get("incident_id"), meta.get("version")) != (incident_id, version):
            log.warning("overlay_v%d.json of %s does not match the brief (incident %r, version %r): not sent",
                        version, incident_id, meta.get("incident_id"), meta.get("version"))
            return None
        return overlay

    def latest_overlay(self, incident_id: str) -> dict:
        """The last overlay.updated payload ({"version", "overlay"}) a late client missed: the newest brief
        version with a valid overlay file."""
        with self._lock(incident_id):
            self._engine(incident_id)
            for v in reversed(self._brief_versions(incident_id)):
                overlay = self._read_overlay(incident_id, v)
                if overlay is not None:
                    return {"version": v, "overlay": overlay}
        raise NotFound(f"no overlay yet for {incident_id}")

    def _drain_updates(self, incident_id: str) -> None:
        """update_incident with every change note queued so far; again if more arrived meanwhile."""
        while True:
            with self._lock(incident_id):
                notes = self._notes.pop(incident_id, [])
            if not notes:
                return
            if self.pipeline(incident_id) != "orchestrator":
                self._rules_update(incident_id)
                return
            t0 = time.perf_counter()
            try:
                r = orchestrator.update_incident(incident_id, notes)
            except ValueError as e:
                if "awaiting" in str(e):   # held for the dispatcher: review_brief drains these later
                    with self._lock(incident_id):
                        self._notes[incident_id][:0] = notes
                    return
                self._fall_back(incident_id, f"orchestrator update failed: {e}")
                self._rules_update(incident_id)
                return
            except Exception as e:  # noqa: BLE001
                log.exception("orchestrator update failed for %s", incident_id)
                self._fall_back(incident_id, f"orchestrator update failed: {type(e).__name__}: {e}")
                self._rules_update(incident_id)
                return
            self._publish_run(incident_id, r, t0)
            if r.status != "briefed":
                return

    def review(self, incident_id: str, drop: Sequence[str] = ()) -> Brief:
        """The dispatcher approves a held brief, or publishes it without the considerations in drop."""
        with self._lock(incident_id):
            self._engine(incident_id)
        if self.pipeline(incident_id) != "orchestrator":
            raise Conflict(f"incident {incident_id} is on the rules pipeline: its briefs are never held")
        return self._submit(incident_id, self._review_job, incident_id, list(drop)).result()

    def _review_job(self, incident_id: str, drop: list[str]) -> Brief:
        t0 = time.perf_counter()
        r = orchestrator.review_brief(incident_id, drop)   # ValueError -> 422
        self._publish_run(incident_id, r, t0)
        self._drain_updates(incident_id)
        assert r.brief is not None
        return r.brief

    def resolve_contradiction(self, incident_id: str, key: str, value: Any) -> IncidentRecord:
        with self._lock(incident_id):
            eng = self._engine(incident_id)
            if not any(c.key == key for c in eng.record.contradictions):
                raise NotFound(f"no contradiction on {key}")
            eng.resolve(key, value)
            self.ws.append_jsonl(self._dir(incident_id) / "inputs.jsonl",
                                 {"type": "resolve", "key": key, "value": value})
            self._save(eng.record)
            self._emit_incident(eng.record)
            self._emit(incident_id, "copilot.suggestion",
                       {"suggestions": [s.model_dump() for s in self.copilot(incident_id) if s.status == "open"]})
            if eng.record.confirmed_building_id:
                self._queue_update(incident_id, [f"Dispatcher resolved the contradiction on {key}: {value}"])
            return eng.record

    # ---------------- fast path (FAST_PIPELINE=1, docs/FAST_PIPELINE.md) ----------------
    def fast_enabled(self) -> bool:
        return self.settings.fast_pipeline and fast_path.available(self.fast_model, self.settings.overlay_model)

    def _drop_fast(self, incident_id: str) -> None:
        with self._guard:
            st = self._fast.pop(incident_id, None)
        if st is not None and st.timer is not None:
            st.timer.cancel()

    def _fast_state(self, incident_id: str) -> Optional[FastState]:
        """The incident's fast-path state while the fast path owns it (None after a fall back)."""
        st = self._fast.get(incident_id)
        return st if st is not None and not st.failed else None

    def _fast_schedule(self, incident_id: str, call_id: Optional[str]) -> bool:
        """A change after confirmation: (re)arm the 1 s debounce of the next run. False when the fast path
        does not own the incident. A call that already had its FAST_RUNS_PER_CALL runs gets no more; what it
        adds is in the transcript the next call's run reads."""
        with self._guard:
            st = self._fast_state(incident_id)
            if st is None:
                return False
            if call_id is not None and st.runs[call_id] >= FAST_RUNS_PER_CALL:
                log.info("fast path %s: call %s already had %d runs; change not run", incident_id, call_id,
                         FAST_RUNS_PER_CALL)
                return True
            if st.timer is not None:
                st.timer.cancel()
            st.pending_call = call_id
            st.timer = threading.Timer(FAST_DEBOUNCE_S, self._fast_fire, (incident_id, st))
            st.timer.daemon = True
            st.timer.start()
        return True

    def _fast_fire(self, incident_id: str, st: FastState) -> None:
        with self._guard:
            if self._fast.get(incident_id) is not st:
                return
            st.timer, call_id = None, st.pending_call
        self._fast_start(incident_id, call_id)

    def _fast_start(self, incident_id: str, call_id: Optional[str]) -> None:
        with self._guard:
            st = self._fast_state(incident_id)
            if st is None:
                return
            if call_id is not None:
                if st.runs[call_id] >= FAST_RUNS_PER_CALL:
                    return
                st.runs[call_id] += 1
            st.started += 1
            st.futures = [f for f in st.futures if not f.done()]
            st.futures.append(self._fast_pool.submit(self._fast_run, incident_id, st, st.started))

    def _fast_progress(self, incident_id: str, status: str, done: bool) -> None:
        todos = [{"content": t, "status": "completed" if done else "in_progress"} for t in FAST_TODOS]
        self._emit(incident_id, "dossier.progress", Progress(
            incident_id=incident_id, status=status, version=len(self._brief_versions(incident_id)) + (0 if done else 1),
            todos=todos).model_dump(mode="json"))

    def _fast_run(self, incident_id: str, st: FastState, gen: int) -> None:
        """One run: the model call and checks (no lock held), then, under the incident's lock, number it and
        emit overlay.updated -> brief.updated (same version) -> incident.updated (-> metric.tick for v1).
        An older run that finishes after a newer one was emitted is dropped: the last one wins."""
        t_run = time.perf_counter()
        try:
            self._fast_progress(incident_id, "running", False)
            with self._lock(incident_id):
                rec = self._engine(incident_id).record.model_copy(deep=True)
            prep = fast_path.prepare(rec, st.building_id, self._agents_ws(incident_id), model=self.fast_model,
                                     model_override=self.settings.overlay_model)
        except Exception as e:  # noqa: BLE001 - FastPathFailed, or the layers / context could not be read
            log.warning("fast path %s run %d failed: %s", incident_id, gen, e)
            self.metrics.record(incident_id, "fast_path", _ms(t_run), run=gen, ok=False,
                                attempts=getattr(e, "attempts", None), timings=getattr(e, "timings", None),
                                errors=getattr(e, "errors", [str(e)])[:5])
            self._fast_failed(incident_id, st, gen)
            return
        t_commit = time.perf_counter()
        with self._lock(incident_id):
            if self._fast.get(incident_id) is not st or st.failed or gen < st.emitted:
                log.info("fast path %s run %d dropped: a newer run was emitted or the path changed", incident_id, gen)
                return
            versions = self._brief_versions(incident_id)
            version = (versions[-1] + 1) if versions else 1
            rec = self._engine(incident_id).record
            try:
                ov, brief = fast_path.commit(prep, rec.model_copy(deep=True), self._agents_ws(incident_id), version)
            except Exception as e:  # noqa: BLE001 - nothing was written
                log.exception("fast path %s run %d could not be committed", incident_id, gen)
                commit_error: Optional[Exception] = e
            else:
                commit_error = None
                st.emitted = gen
                rec.status = "confirmed" if version == 1 else "updated"
                self._save(rec)
                self._emit(incident_id, "overlay.updated",
                           {"version": version, "overlay": json.loads(fast_path.overlay_mod.dump(ov))})
                self._emit(incident_id, "brief.updated", self._brief_event(brief, "fast", "briefed"))
                self._emit_incident(rec)
        if commit_error is not None:
            self._fast_failed(incident_id, st, gen)
            return
        self.metrics.log_brief(brief)
        timings = {**prep.timings, "commit_ms": round(_ms(t_commit), 1), "run_s": round(time.perf_counter() - t_run, 2)}
        self.metrics.record(incident_id, "fast_path", _ms(t_run), run=gen, ok=True, version=version,
                            attempts=prep.attempts, timings=timings)
        log.info("fast path %s run %d emitted v%d (attempts %d, %s)", incident_id, gen, version, prep.attempts, timings)
        if version == 1:
            row = self.metrics.record(incident_id, "time_to_brief", _ms(st.t0), cached_dossier=prep.prefetched_at
                                      is not None, building_id=st.building_id, version=1, pipeline="fast")
            self._emit(incident_id, "metric.tick", row)
        self._fast_progress(incident_id, "done", True)

    def _fast_failed(self, incident_id: str, st: FastState, gen: int) -> None:
        """Two attempts failed. Nothing emitted yet for this building: the agent path takes the incident, exactly
        as without FAST_PIPELINE. A version is already out: keep it (nothing invalid is sent); the next change
        runs again."""
        with self._guard:
            if self._fast.get(incident_id) is not st or st.failed:
                return
            if st.emitted:
                log.warning("fast path %s run %d failed; v%d stays", incident_id, gen, len(self._brief_versions(incident_id)))
                return
            st.failed = True
            if st.timer is not None:
                st.timer.cancel()
                st.timer = None
        log.warning("fast path %s: falling back to the agent path", incident_id)
        self.audit(incident_id, "system", "fast_path_fallback", {"run": gen})
        self._submit(incident_id, self._confirm_job, incident_id, st.building_id, "orchestrator", st.t0)

    # ---------------- rules pipeline (fallback) ----------------
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

    def _rules_confirm(self, incident_id: str, building_id: str, t0: float) -> None:
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            dossier, cached = self.dossier(building_id, incident_id=incident_id)
            self.ws.write_json(self._dir(incident_id) / "dossier.json", dossier)
            brief = self._new_brief(incident_id, rec, dossier, force=True)
            rec.status = "confirmed" if brief.version == 1 else "updated"
            self._save(rec)
            self._emit_incident(rec)
        row = self.metrics.record(incident_id, "time_to_brief", _ms(t0), cached_dossier=cached,
                                  building_id=building_id, version=brief.version, pipeline="rules")
        self._emit(incident_id, "metric.tick", row)

    def _rules_update(self, incident_id: str) -> None:
        with self._lock(incident_id):
            rec = self._engine(incident_id).record
            p = self._dir(incident_id) / "dossier.json"
            if not p.exists():   # fell back after the orchestrator wrote the earlier versions
                if not rec.confirmed_building_id:
                    return
                self.ws.write_json(p, self.dossier(rec.confirmed_building_id, incident_id=incident_id)[0])
            self._new_brief(incident_id, rec, self.ws.read_json(p, BuildingDossier))

    def _new_brief(self, incident_id: str, rec: IncidentRecord, dossier: BuildingDossier,
                   force: bool = False) -> Brief:
        versions = self._brief_versions(incident_id)
        prev = self._read_brief(incident_id, versions[-1]) if versions else None
        t0 = time.perf_counter()
        brief = build_brief(rec, dossier, (versions[-1] + 1) if versions else 1, self.cfg,
                            prev=prev, crew_notes=self._crew_notes(dossier.building_id))
        brief.published = True               # the rules pipeline never holds a brief for review
        if prev and not force and not brief.changes_since_last:
            return prev                      # nothing visible changed: no version churn
        self.metrics.record(incident_id, "brief", _ms(t0), version=brief.version)
        self._agents_ws(incident_id).write(f"brief_v{brief.version}.json", brief.model_dump_json(indent=2))
        self.metrics.log_brief(brief)
        if prev and rec.status in ("confirmed", "updated"):
            rec.status = "updated"
            self._save(rec)
        reason = (self.ws.read_json(self._dir(incident_id) / "pipeline.json").get("reason")
                  if (self._dir(incident_id) / "pipeline.json").exists() else None)
        self._emit(incident_id, "brief.updated", self._brief_event(brief, "rules", "briefed", fallback_reason=reason))
        self._emit_overlay(incident_id, brief.version)
        return brief

    # ---------------- briefs (one store: the orchestrator workspace) ----------------
    def _brief_versions(self, incident_id: str) -> list[int]:
        return sorted(int(m.group(1)) for rel in self._agents_ws(incident_id).list("brief_v")
                      if (m := re.fullmatch(r"brief_v(\d+)\.json", rel)))

    def _read_brief(self, incident_id: str, version: int) -> Brief:
        text = self._agents_ws(incident_id).read_or_none(f"brief_v{version}.json")
        if text is None:
            raise NotFound(f"brief v{version} of {incident_id}")
        return Brief.model_validate_json(text)

    def briefs(self, incident_id: str) -> list[Brief]:
        with self._lock(incident_id):
            self._engine(incident_id)
            return [self._read_brief(incident_id, v) for v in self._brief_versions(incident_id)]

    def brief(self, incident_id: str, version: Optional[int] = None, published_only: bool = False) -> Brief:
        with self._lock(incident_id):
            self._engine(incident_id)
            versions = self._brief_versions(incident_id)
            if version is not None:
                return self._read_brief(incident_id, version)
            for v in reversed(versions):
                b = self._read_brief(incident_id, v)
                if b.published or not published_only:
                    return b
            raise NotFound(f"no brief yet for {incident_id} (awaiting location confirmation)")

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
            out = copilot.suggestions(rec, self._copilot_states(incident_id))
            if missing := self._not_in_catalog(incident_id, rec):
                out.insert(0, missing)
            return out

    def _not_in_catalog(self, incident_id: str, rec: IncidentRecord) -> Optional[CopilotSuggestion]:
        """An address was reported, nothing matched it (catalog, stored data, fixtures) and nothing is confirmed: the
        dispatcher sees "Address not in catalog" in the copilot list (the UI's suggested questions)."""
        address = rec.facts.get("location.address")
        p = self._dir(incident_id) / "candidates.json"
        if address is None or rec.confirmed_building_id or not p.exists() or self.ws.read_json(p):
            return None
        sid = copilot.suggestion_id(incident_id, "location.not_in_catalog")
        status = self._copilot_states(incident_id).get(sid, "open")
        return CopilotSuggestion(id=sid, incident_id=incident_id, field="location.address",
                                 question=f"Address not in catalog: {address.value}. Can you confirm the address?",
                                 status=status)  # type: ignore[arg-type]

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

    # ---------------- feedback (FR-15): building memory, one path ----------------
    def add_feedback(self, incident_id: str, fact_key: str, verdict: str, note: Optional[str],
                     role: str) -> CrewFeedback:
        with self._lock(incident_id):
            self._engine(incident_id)
            fb = memory.feedback(incident_id, fact_key, verdict, note, role, workspace_root=self.agents_root)
        self.audit(incident_id, role, "feedback", {"fact_key": fact_key, "verdict": verdict})
        return fb

    def feedback(self, incident_id: str) -> list[CrewFeedback]:
        with self._lock(incident_id):
            building_id = self._engine(incident_id).record.confirmed_building_id
        if not building_id:
            return []
        return [r.feedback for r in memory.records(building_id) if r.feedback.incident_id == incident_id]

    def _crew_notes(self, building_id: str) -> list[CrewFeedback]:
        return [r.feedback for r in memory.building_memory(building_id)][-20:]

    # ---------------- audit (FR-14) ----------------
    def audit(self, incident_id: str, role: str, action: str, detail: Optional[dict] = None) -> None:
        if not self.ws.incident_exists(incident_id):
            return
        self.ws.append_jsonl(self._dir(incident_id) / "audit.jsonl", {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role,
            "action": action, "detail": detail or {}})

    def audit_log(self, incident_id: str) -> list[dict]:
        self._engine(incident_id)
        return self.ws.read_jsonl(self._dir(incident_id) / "audit.jsonl")
