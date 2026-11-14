"""FlameScout API (PRD Section 11). Run:  uvicorn services.api.main:app --reload --port 8000

REST under /api, live events on /ws/incidents/{id}. Role is a demo header
(X-Role: dispatcher|officer|admin), used for the audit log, not for security.
SIMULATION ONLY: nothing here dials a phone number or connects to a 911 line.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import (Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from flamescout_schemas import (Brief, BuildingDossier, CallSegment, CopilotSuggestion,
                                CrewFeedback, IncidentRecord, LocationCandidate)

from services.agents.extract import GeminiExtractor, get_extractor
from services.agents.llm import gemini_configured

from .config import ROOT, Settings
from .events import EventBus
from .service import Conflict, IncidentService
from .store import NotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("flamescout.api")
CALLS_DIR = ROOT / "data" / "fixtures" / "calls"
Role = Literal["dispatcher", "officer", "admin"]


# ---------------- request bodies ----------------

class DataModeIn(BaseModel):
    mode: Literal["live", "fixture"]


class IncidentIn(BaseModel):
    incident_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class SimulateIn(BaseModel):
    script_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,16}$", examples=["S2"])
    incident_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    realtime: bool = False              # True: replay with original timing in the background
    speed: float = Field(default=1.0, gt=0, le=20)
    confirm_building_id: Optional[str] = None   # simulate the dispatcher's tap ...
    confirm_after_segment: Optional[int] = None  # ... after this segment index (default: when located)


class ConfirmIn(BaseModel):
    # null = "none of these": the orchestrator stops, no brief is written
    building_id: Optional[str] = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ReviewIn(BaseModel):
    drop: list[str] = []   # consideration ids to leave out; empty = approve as is


class ResolveIn(BaseModel):
    key: str
    value: Any


class FeedbackIn(BaseModel):
    fact_key: str = Field(min_length=1, max_length=120)
    verdict: Literal["wrong", "outdated", "confirmed", "correct"]   # memory.VERDICTS
    note: Optional[str] = Field(default=None, max_length=1000)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    bus = EventBus()
    svc = IncidentService(settings, bus)
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for t in tasks:
            t.cancel()
        svc.shutdown()

    app = FastAPI(title="FlameScout API", version="0.1.0", lifespan=lifespan,
                  description="Advisory size-up briefs from simulated dispatch calls. Simulation only.")
    app.state.svc, app.state.bus = svc, bus
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, e: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"not found: {e.args[0] if e.args else ''}"})

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, e: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(e)})

    @app.exception_handler(ValueError)
    async def _bad(_: Request, e: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(e)})

    def role(x_role: Role = Header("dispatcher")) -> str:
        return x_role

    # ---------------- health / admin ----------------
    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "data_mode": svc.data_mode, "pipeline": settings.pipeline,
                "extractor": "gemini" if isinstance(get_extractor(), GeminiExtractor) else "keyword",
                "gemini": {"fast": gemini_configured("fast"), "vision": gemini_configured("vision")},
                "tiger": svc.metrics.tiger_enabled, "geocoder": svc.providers.geocoder is not None,
                "live_fallbacks": svc.providers.misses[-10:]}

    @app.post("/api/admin/data-mode")
    def set_data_mode(body: DataModeIn, r: str = Depends(role)) -> dict:
        if r != "admin":
            raise HTTPException(403, "admin role required (X-Role: admin)")
        svc.set_data_mode(body.mode)
        return {"data_mode": svc.data_mode}

    # ---------------- incidents CRUD ----------------

    @app.post("/api/incidents", status_code=201, response_model=IncidentRecord)
    def create_incident(body: IncidentIn = IncidentIn()) -> IncidentRecord:
        return svc.create_incident(body.incident_id)

    @app.get("/api/incidents", response_model=list[IncidentRecord])
    def list_incidents() -> list[IncidentRecord]:
        return svc.list_incidents()

    @app.get("/api/incidents/{incident_id}", response_model=IncidentRecord)
    def get_incident(incident_id: str) -> IncidentRecord:
        return svc.get_incident(incident_id)

    @app.delete("/api/incidents/{incident_id}", status_code=204)
    def delete_incident(incident_id: str) -> None:
        svc.delete_incident(incident_id)

    # ---------------- segments / simulated calls ----------------
    @app.post("/api/incidents/{incident_id}/segments")
    def ingest_segment(incident_id: str, seg: CallSegment) -> dict:
        """One transcript segment (from STT or the text feed). Returns fusion changes."""
        out = svc.ingest_segment(incident_id, seg)
        return {**out, "incident": out["incident"].model_dump(mode="json")}

    def _load_script(script_id: str) -> list[CallSegment]:
        p = CALLS_DIR / f"{script_id.lower()}.json"
        if not p.exists():
            raise NotFound(f"script {script_id}")
        return [CallSegment(**s) for s in json.loads(p.read_text())["segments"]]

    def _should_confirm(body: SimulateIn, i: int, out: dict, confirmed: bool) -> bool:
        if not body.confirm_building_id or confirmed:
            return False
        if body.confirm_after_segment is not None:
            return i == body.confirm_after_segment
        return any(c.building_id == body.confirm_building_id for c in (out.get("candidates") or []))

    async def _replay(body: SimulateIn, incident_id: str, segs: list[CallSegment], speed: float) -> None:
        confirmed, t_prev = False, segs[0].t_start if segs else 0.0
        for i, seg in enumerate(segs):
            await asyncio.sleep(max(0.0, seg.t_start - t_prev) / speed)
            t_prev = seg.t_start
            out = await asyncio.to_thread(svc.ingest_segment, incident_id, seg)
            if _should_confirm(body, i, out, confirmed):
                await asyncio.to_thread(svc.confirm_location, incident_id, body.confirm_building_id)
                confirmed = True

    async def _replay_logged(body: SimulateIn, incident_id: str, segs: list[CallSegment]) -> None:
        try:
            await _replay(body, incident_id, segs, body.speed)
        except Exception:  # noqa: BLE001 - background task: log, never crash the server
            log.exception("replay of %s into %s failed", body.script_id, incident_id)

    @app.get("/api/calls/scripts")
    def list_scripts() -> list[dict]:
        out = []
        for p in sorted(CALLS_DIR.glob("*.json")):
            d = json.loads(p.read_text())
            out.append({"script_id": d.get("script_id", p.stem.upper()), "description": d.get("description"),
                        "segments": len(d.get("segments", []))})
        return out

    @app.post("/api/calls/simulate", status_code=202)
    async def simulate(body: SimulateIn) -> dict:
        segs = _load_script(body.script_id)
        if body.incident_id and svc.ws.incident_exists(body.incident_id):
            incident_id = body.incident_id
        else:
            incident_id = (await asyncio.to_thread(svc.create_incident, body.incident_id)).incident_id
        if body.realtime:
            task = asyncio.create_task(_replay_logged(body, incident_id, segs))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return {"incident_id": incident_id, "status": "streaming", "segments": len(segs),
                    "ws": f"/ws/incidents/{incident_id}"}
        await _replay(body, incident_id, segs, speed=float("inf"))
        await asyncio.to_thread(svc.wait, incident_id)   # briefs are written in the background
        rec = svc.get_incident(incident_id)
        return {"incident_id": incident_id, "status": "done", "segments": len(segs),
                "incident": rec.model_dump(mode="json")}

    # ---------------- location (FR-3) ----------------
    @app.get("/api/incidents/{incident_id}/candidates", response_model=list[LocationCandidate])
    def get_candidates(incident_id: str, refresh: bool = False) -> list[LocationCandidate]:
        return svc.locate(incident_id) if refresh else svc.candidates(incident_id)

    @app.post("/api/incidents/{incident_id}/confirm-location", status_code=202)
    def confirm_location(incident_id: str, body: ConfirmIn, r: str = Depends(role)) -> dict:
        """Returns at once. The brief arrives as `brief.updated` on the WebSocket (orchestrator: ~35-40 s),
        with `status` "briefed" or "awaiting_approval" (then POST /review)."""
        out = svc.confirm_location(incident_id, body.building_id)
        svc.audit(incident_id, r, "confirm_location", {"building_id": body.building_id})
        return out

    @app.post("/api/incidents/{incident_id}/review", response_model=Brief)
    def review(incident_id: str, body: ReviewIn = ReviewIn(), r: str = Depends(role)) -> Brief:
        """Publish a brief held for low-confidence considerations, optionally without some of them."""
        brief = svc.review(incident_id, body.drop)
        svc.audit(incident_id, r, "review_brief", {"drop": body.drop, "version": brief.version})
        return brief

    @app.post("/api/incidents/{incident_id}/contradictions/resolve", response_model=IncidentRecord)
    def resolve(incident_id: str, body: ResolveIn, r: str = Depends(role)) -> IncidentRecord:
        rec = svc.resolve_contradiction(incident_id, body.key, body.value)
        svc.audit(incident_id, r, "resolve_contradiction", body.model_dump())
        return rec

    # ---------------- dossier + briefs (FR-4, FR-6, FR-9) ----------------
    @app.get("/api/buildings")
    def list_buildings() -> list[dict]:
        return svc.buildings()

    @app.get("/api/buildings/{building_id}/dossier", response_model=BuildingDossier)
    def get_dossier(building_id: str, refresh: bool = False) -> BuildingDossier:
        return svc.dossier(building_id, refresh=refresh)[0]

    @app.get("/api/incidents/{incident_id}/briefs", response_model=list[Brief])
    def list_briefs(incident_id: str, r: str = Depends(role)) -> list[Brief]:
        out = svc.briefs(incident_id)
        svc.audit(incident_id, r, "view_briefs", {"versions": [b.version for b in out]})
        return out

    @app.get("/api/incidents/{incident_id}/briefs/latest", response_model=Brief)
    def latest_brief(incident_id: str, published_only: bool = False, r: str = Depends(role)) -> Brief:
        """published_only=true for the crew view: skips a draft awaiting the dispatcher's review."""
        b = svc.brief(incident_id, published_only=published_only)
        svc.audit(incident_id, r, "view_brief", {"version": b.version})
        return b

    @app.get("/api/incidents/{incident_id}/briefs/{version}", response_model=Brief)
    def get_brief(incident_id: str, version: int, r: str = Depends(role)) -> Brief:
        b = svc.brief(incident_id, version)
        svc.audit(incident_id, r, "view_brief", {"version": version})
        return b

    # ---------------- copilot (FR-11) ----------------
    @app.get("/api/incidents/{incident_id}/copilot", response_model=list[CopilotSuggestion])
    def get_copilot(incident_id: str, include_closed: bool = False) -> list[CopilotSuggestion]:
        out = svc.copilot(incident_id)
        return out if include_closed else [s for s in out if s.status == "open"]

    @app.post("/api/copilot/suggestion/{suggestion_id}/dismiss", response_model=CopilotSuggestion)
    def dismiss(suggestion_id: str) -> CopilotSuggestion:
        return svc.set_suggestion(suggestion_id, "dismissed")

    @app.post("/api/copilot/suggestion/{suggestion_id}/asked", response_model=CopilotSuggestion)
    def mark_asked(suggestion_id: str) -> CopilotSuggestion:
        return svc.set_suggestion(suggestion_id, "asked")

    # ---------------- feedback + audit (FR-14, FR-15) ----------------

    @app.post("/api/incidents/{incident_id}/feedback", status_code=201, response_model=CrewFeedback)
    def add_feedback(incident_id: str, body: FeedbackIn, r: str = Depends(role)) -> CrewFeedback:
        return svc.add_feedback(incident_id, body.fact_key, body.verdict, body.note, r)

    @app.get("/api/incidents/{incident_id}/feedback", response_model=list[CrewFeedback])
    def list_feedback(incident_id: str) -> list[CrewFeedback]:
        return svc.feedback(incident_id)

    @app.get("/api/incidents/{incident_id}/audit")
    def audit_log(incident_id: str) -> list[dict]:
        return svc.audit_log(incident_id)

    # ---------------- metrics (FR-13) ----------------
    @app.get("/api/metrics/time-to-brief")
    def time_to_brief(limit: int = Query(100, ge=1, le=1000)) -> dict:
        return svc.metrics.time_to_brief(limit)

    @app.get("/api/metrics/stages")
    def stages() -> dict:
        return svc.metrics.stage_summary()

    # ---------------- websocket ----------------
    @app.websocket("/ws/incidents/{incident_id}")
    async def ws_incident(websocket: WebSocket, incident_id: str) -> None:
        await websocket.accept()
        q = bus.subscribe(incident_id)
        try:
            try:   # snapshot first so a late client has the full picture
                rec = await asyncio.to_thread(svc.get_incident, incident_id)
                await websocket.send_json({"type": "incident.updated", "incident_id": incident_id,
                                           "trace_id": svc.trace_id(incident_id), "snapshot": True,
                                           "payload": {"incident": rec.model_dump(mode="json")}})
            except (NotFound, ValueError):
                await websocket.close(code=4404)
                return
            while True:
                ev = await q.get()
                await websocket.send_json(ev.model_dump(mode="json"))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(incident_id, q)

    return app


app = create_app()
