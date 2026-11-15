"""The API on PIPELINE=orchestrator, offline: no model is configured, so the orchestrator writes its
facts-only brief (fallback_reason set). That exercises the wiring: candidates from the orchestrator's
locator, start/resume/update on the incident's worker, one brief store, events, memory feedback,
and the fall back to the rules pipeline."""
import pytest
from fastapi.testclient import TestClient

from services.agents import orchestrator
from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES, load_script

BUILDING = "fake_wilshire_tower"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_CONFIRM_LOCATION", "0")   # these tests drive the dispatcher's confirmation
    app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                              tiger_url=None, cors_origins=["*"], pipeline="orchestrator"))
    with TestClient(app) as c:
        yield c


def post_call(client, iid: str, script: str, call_id: str) -> None:
    for s in load_script(script):
        if s.call_id == call_id:
            client.post(f"/api/incidents/{iid}/segments", json=s.model_dump())
    client.app.state.svc.wait(iid)


def events(client, iid: str, type_: str) -> list[dict]:
    return [e.payload for e in client.app.state.bus.history(iid) if e.type == type_]


def test_s2_runs_on_the_orchestrator(client):
    svc = client.app.state.svc
    client.post("/api/incidents", json={"incident_id": "inc_s2"})
    post_call(client, "inc_s2", "S2", "c1")
    cands = client.get("/api/incidents/inc_s2/candidates").json()
    assert cands[0]["building_id"] == BUILDING
    assert {"coordinates", "match_reasons", "scan_available"} <= set(cands[0])
    progress = events(client, "inc_s2", "dossier.progress")
    assert progress[-1]["status"] == "awaiting_confirmation" and "todos" in progress[-1]

    r = client.post("/api/incidents/inc_s2/confirm-location", json={"building_id": BUILDING})
    assert r.status_code == 202 and r.json()["pipeline"] == "orchestrator"
    svc.wait("inc_s2")
    b1 = client.get("/api/incidents/inc_s2/briefs/latest").json()
    assert (b1["version"], b1["building_id"], b1["published"]) == (1, BUILDING, True)
    [ev] = events(client, "inc_s2", "brief.updated")
    assert ev["pipeline"] == "orchestrator" and ev["status"] == "briefed"
    assert ev["fallback_reason"] and b1["gaps"][0].startswith("Considerations withheld")

    # call 2: floor 4 -> update_incident. Offline each update ends before the next segment arrives,
    # so every segment with new facts is its own version (live, one ~30 s update batches them).
    post_call(client, "inc_s2", "S2", "c2")
    briefs = client.get("/api/incidents/inc_s2/briefs").json()
    assert [b["version"] for b in briefs] == list(range(1, len(briefs) + 1)) and len(briefs) >= 2
    assert any("Floor contradicted: 3 vs 4 (Call 2)" in b["changes_since_last"] for b in briefs[1:])
    assert all(e["pipeline"] == "orchestrator" for e in events(client, "inc_s2", "brief.updated"))
    assert svc.pipeline("inc_s2") == "orchestrator"

    # one feedback path: memory.feedback(), read back from the building's memory
    fb = client.post("/api/incidents/inc_s2/feedback", headers={"X-Role": "officer"},
                     json={"fact_key": "incident.floor", "verdict": "confirmed", "note": "it was 4"})
    assert fb.status_code == 201 and fb.json()["building_id"] == BUILDING and fb.json()["verdict"] == "correct"
    assert [f["note"] for f in client.get("/api/incidents/inc_s2/feedback").json()] == ["it was 4"]
    bad = client.post("/api/incidents/inc_s2/feedback", json={"fact_key": "nope", "verdict": "wrong"})
    assert bad.status_code == 422

    # nothing is held: review is a 422, not a crash
    assert client.post("/api/incidents/inc_s2/review", json={"drop": []}).status_code == 422


def test_building_unknown_to_the_orchestrator_uses_the_rules_pipeline(client):
    r = client.post("/api/calls/simulate", json={"script_id": "S3", "incident_id": "inc_s3"}).json()
    cands = [c["building_id"] for c in client.get("/api/incidents/inc_s3/candidates").json()]
    assert "harbor_plaza_pine" in cands   # the orchestrator's locator found nothing: rules locator
    r = client.post("/api/incidents/inc_s3/confirm-location", json={"building_id": "harbor_plaza_pine"})
    assert r.status_code == 202 and r.json()["pipeline"] == "rules"
    client.app.state.svc.wait("inc_s3")
    b = client.get("/api/incidents/inc_s3/briefs/latest").json()
    assert b["version"] == 1 and b["published"]
    assert client.post("/api/incidents/inc_s3/review", json={}).status_code == 409


def test_orchestrator_failure_falls_back_to_rules(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(orchestrator, "resume_incident", boom)
    client.post("/api/incidents", json={"incident_id": "inc_fb"})
    post_call(client, "inc_fb", "S2", "c1")
    client.post("/api/incidents/inc_fb/confirm-location", json={"building_id": BUILDING})
    svc = client.app.state.svc
    svc.wait("inc_fb")
    assert svc.pipeline("inc_fb") == "rules"
    b1 = client.get("/api/incidents/inc_fb/briefs/latest").json()
    assert b1["version"] == 1 and b1["published"]
    [ev] = events(client, "inc_fb", "brief.updated")
    assert ev["pipeline"] == "rules" and "model down" in ev["fallback_reason"]

    post_call(client, "inc_fb", "S2", "c2")   # later updates stay on rules
    assert client.get("/api/incidents/inc_fb/briefs/latest").json()["version"] >= 2
    assert {e["pipeline"] for e in events(client, "inc_fb", "brief.updated")} == {"rules"}


def test_none_of_these(client):
    client.post("/api/incidents", json={"incident_id": "inc_rj"})
    post_call(client, "inc_rj", "S2", "c1")
    r = client.post("/api/incidents/inc_rj/confirm-location", json={"building_id": None})
    assert r.status_code == 202 and r.json()["status"] == "rejected"
    client.app.state.svc.wait("inc_rj")
    assert events(client, "inc_rj", "dossier.progress")[-1]["status"] == "rejected"
    assert client.get("/api/incidents/inc_rj/briefs/latest").status_code == 404


def _overlay(iid: str, version: int) -> str:
    import json
    return json.dumps({"incident_id": iid, "model_id": "fx-wilshire-001", "version": version,
                       "as_of": "2026-11-15T03:12:40Z", "changes_since_last": [], "occupants": [],
                       "fire": [], "hazards": [], "blocked": [], "contradictions": [], "objectives": [],
                       "considerations": [], "routes": []})


def test_overlay_updated_follows_brief_updated(client):
    svc = client.app.state.svc
    client.post("/api/incidents", json={"incident_id": "inc_ov"})
    post_call(client, "inc_ov", "S2", "c1")
    svc._agents_ws("inc_ov").write("overlay_v1.json", _overlay("inc_ov", 1))   # the producer's file
    client.post("/api/incidents/inc_ov/confirm-location", json={"building_id": BUILDING})
    svc.wait("inc_ov")
    hist = client.app.state.bus.history("inc_ov")
    i = next(n for n, e in enumerate(hist) if e.type == "brief.updated")
    ev = hist[i + 1]
    assert ev.type == "overlay.updated" and ev.trace_id == hist[i].trace_id
    assert ev.payload["version"] == hist[i].payload["version"] == ev.payload["overlay"]["version"] == 1
    assert ev.payload["overlay"]["incident_id"] == "inc_ov"


def test_overlay_missing_or_mismatched_is_not_sent(client):
    svc = client.app.state.svc
    client.post("/api/incidents", json={"incident_id": "inc_ov2"})
    ws = svc._agents_ws("inc_ov2")
    svc._emit_overlay("inc_ov2", 1)                        # no file
    ws.write("overlay_v2.json", _overlay("inc_ov2", 3))    # wrong version
    svc._emit_overlay("inc_ov2", 2)
    ws.write("overlay_v3.json", _overlay("other", 3))      # wrong incident
    svc._emit_overlay("inc_ov2", 3)
    ws.write("overlay_v4.json", "{not json")
    svc._emit_overlay("inc_ov2", 4)
    assert events(client, "inc_ov2", "overlay.updated") == []
