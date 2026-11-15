"""Mock of the incident API + WebSocket for the web app's live overlay. DEV ONLY, NOT DEPLOYED.

Minimal REST, as the real API answers it for a fresh incident: POST /api/incidents (new id), GET /api/incidents/{id}
(any id; confirmed_building_id 209217448 = Fairdale, confirmed_by "auto", status briefing), GET /api/calls/scripts (from
data/fixtures/calls/manifest.json), POST /api/calls/simulate (202, nothing replayed), candidates/copilot ([]),
briefs/latest and overlay/latest (404, as before the first brief).
Serves /ws/incidents/{id} and, on each connection, sends what the API sends (services/api/main.py and service.py):
first an incident.updated snapshot (with the confirmed fields), then overlay.updated events in the real Event envelope

    {"type": "overlay.updated", "incident_id": id, "ts": ..., "trace_id": ..., "payload": {"version": N, "overlay": {...}}}

with Fairdale's S4 overlays (apps/web/public/house-viewer/incident/examples/fairdale_s4_v1.json / _v2.json): v1 after
--v1 seconds (default 1) and v2 after --v2 seconds (default 6). As in the API, overlay.incident_id is the incident id.
  --invalid   a third version (v3) that is not valid for the plan (unknown room id): the viewer rejects it, keeps v2
  --unknown   every overlay with a model_id that is in no catalog: the web app shows "Unknown building"
              (the incident then has no confirmed building, so the MDT keeps its default house)
  --off-catalog  confirmed_building_id "99999999" (in no catalog): the MDT shows "No interior scan on file"
No keys, no network calls, no models.

    python scripts/mock_incident_ws.py [--port 8765] [--v1 1] [--v2 6] [--invalid] [--unknown] [--off-catalog]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "schemas"))
from flamescout_schemas import Event  # noqa: E402  (the API's envelope)

EXAMPLES = ROOT / "apps" / "web" / "public" / "house-viewer" / "incident" / "examples"
UNKNOWN_MODEL = "unknownModel0"
CALLS = ROOT / "data" / "fixtures" / "calls"
FAIRDALE, OFF_CATALOG = "209217448", "99999999"
GROUP_ORDER = {"showcase": 0, "test": 1}


def scripts() -> list[dict]:
    """Like GET /api/calls/scripts: the manifest's visible scripts, showcase first."""
    manifest = json.loads((CALLS / "manifest.json").read_text(encoding="utf-8"))["scripts"]
    rows = []
    for n, m in enumerate(manifest):
        f = CALLS / f"{str(m['id']).lower()}.json"
        if not m.get("visible", True) or not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        rows.append((GROUP_ORDER.get(m.get("group", "test"), 2), n, {
            "script_id": d.get("script_id", m["id"]), "description": d.get("description"),
            "segments": len(d.get("segments", [])), "title": m.get("title"), "group": m.get("group", "test"),
            "inconsistent": bool(m.get("inconsistent", False)), "house": m.get("house")}))
    return [r for _, _, r in sorted(rows, key=lambda r: r[:2])]


def incident(incident_id: str, args: argparse.Namespace, confirmed: bool = True) -> dict:
    bid = None if not confirmed else OFF_CATALOG if args.off_catalog else FAIRDALE
    return {"incident_id": incident_id, "status": "briefing" if bid else "listening", "call_ids": [], "facts": {},
            "evidence": {}, "contradictions": [], "open_questions": [], "confirmed_building_id": bid,
            "confirmed_by": "auto" if bid else None, "ready_to_brief": bool(bid)}


def overlays(incident_id: str, invalid: bool, unknown: bool) -> list[dict]:
    out = []
    for n in (1, 2):
        ov = json.loads((EXAMPLES / f"fairdale_s4_v{n}.json").read_text(encoding="utf-8"))
        ov["incident_id"] = incident_id
        out.append(ov)
    if invalid:
        bad = copy.deepcopy(out[1])
        bad["version"] = 3
        bad["changes_since_last"] = ["Mock: occupants moved to a room that is not in the plan"]
        bad["occupants"][0]["location"] = {"kind": "room", "room_id": "no-such-room"}
        out.append(bad)
    if unknown:
        for ov in out:
            ov["model_id"] = UNKNOWN_MODEL
    return out


def create_app(args: argparse.Namespace) -> FastAPI:
    app = FastAPI(title="FlameScout incident API + WebSocket mock (dev only)")

    @app.get("/api/calls/scripts")
    def list_scripts() -> list[dict]:
        return scripts()

    @app.post("/api/incidents")
    def create() -> dict:   # fresh incident; the snapshot on the socket brings the (auto) confirmation
        return incident(f"inc_{uuid.uuid4().hex[:10]}", args, confirmed=False)

    @app.get("/api/incidents/{incident_id}")
    def get(incident_id: str) -> dict:
        return incident(incident_id, args)

    @app.post("/api/calls/simulate", status_code=202)
    def simulate(body: dict) -> dict:
        return {"incident_id": body.get("incident_id"), "status": "streaming", "segments": 0}

    @app.get("/api/incidents/{incident_id}/candidates")
    @app.get("/api/incidents/{incident_id}/copilot")
    def empty(incident_id: str) -> list:
        return []

    @app.get("/api/incidents/{incident_id}/briefs/latest")
    @app.get("/api/incidents/{incident_id}/overlay/latest")
    def none_yet(incident_id: str) -> dict:
        raise HTTPException(404, "not found: no brief yet")

    @app.websocket("/ws/incidents/{incident_id}")
    async def ws_incident(websocket: WebSocket, incident_id: str) -> None:
        await websocket.accept()
        trace = f"mock-{uuid.uuid4().hex[:8]}"
        delays = [args.v1, args.v2, args.v2 + args.gap]
        try:
            await websocket.send_json({"type": "incident.updated", "incident_id": incident_id, "trace_id": trace,
                                       "snapshot": True, "payload": {"incident": incident(incident_id, args)}})
            t = 0.0
            for delay, ov in zip(delays, overlays(incident_id, args.invalid, args.unknown)):
                await asyncio.sleep(max(0.0, delay - t))
                t = delay
                ev = Event(type="overlay.updated", incident_id=incident_id, trace_id=trace,
                           payload={"version": ov["version"], "overlay": ov})
                await websocket.send_json(ev.model_dump(mode="json"))
            while True:   # stay open like the API until the client leaves
                await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app


def main() -> None:
    p = argparse.ArgumentParser(description="Incident WebSocket mock (dev only, not deployed)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--v1", type=float, default=1.0, help="seconds after connecting until v1")
    p.add_argument("--v2", type=float, default=6.0, help="seconds after connecting until v2")
    p.add_argument("--gap", type=float, default=3.0, help="seconds between v2 and the invalid v3")
    p.add_argument("--invalid", action="store_true", help="send an invalid v3 after v2")
    p.add_argument("--unknown", action="store_true", help="use a model_id that matches no house")
    p.add_argument("--off-catalog", action="store_true", help=f"confirmed building {OFF_CATALOG}, in no catalog")
    args = p.parse_args()
    uvicorn.run(create_app(args), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
