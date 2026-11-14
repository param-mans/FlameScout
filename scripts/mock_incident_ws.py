"""Mock of the incident WebSocket for the web app's live overlay. DEV ONLY, NOT DEPLOYED.

Serves /ws/incidents/{id} and, on each connection, sends what the API sends (services/api/main.py and service.py):
first an incident.updated snapshot (the web app ignores it), then overlay.updated events in the real Event envelope

    {"type": "overlay.updated", "incident_id": id, "ts": ..., "trace_id": ..., "payload": {"version": N, "overlay": {...}}}

with Fairdale's S4 overlays (apps/web/public/house-viewer/incident/examples/fairdale_s4_v1.json / _v2.json): v1 after
--v1 seconds (default 1) and v2 after --v2 seconds (default 6). As in the API, overlay.incident_id is the incident id.
  --invalid   a third version (v3) that is not valid for the plan (unknown room id): the viewer rejects it, keeps v2
  --unknown   every overlay with a model_id that is in no catalog: the web app shows "Unknown building"
No keys, no network calls, no models.

    python scripts/mock_incident_ws.py [--port 8765] [--v1 1] [--v2 6] [--invalid] [--unknown]
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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "schemas"))
from flamescout_schemas import Event  # noqa: E402  (the API's envelope)

EXAMPLES = ROOT / "apps" / "web" / "public" / "house-viewer" / "incident" / "examples"
UNKNOWN_MODEL = "unknownModel0"


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
    app = FastAPI(title="FlameScout incident WebSocket mock (dev only)")

    @app.websocket("/ws/incidents/{incident_id}")
    async def ws_incident(websocket: WebSocket, incident_id: str) -> None:
        await websocket.accept()
        trace = f"mock-{uuid.uuid4().hex[:8]}"
        delays = [args.v1, args.v2, args.v2 + args.gap]
        try:
            await websocket.send_json({"type": "incident.updated", "incident_id": incident_id, "trace_id": trace,
                                       "snapshot": True, "payload": {"incident": {"incident_id": incident_id}}})
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
    args = p.parse_args()
    uvicorn.run(create_app(args), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
