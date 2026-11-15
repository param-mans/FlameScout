import pytest
from fastapi.testclient import TestClient

from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES, load_script


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The rules pipeline (PIPELINE=rules), on the API's own fixture buildings."""
    monkeypatch.setenv("AUTO_CONFIRM_LOCATION", "0")   # these tests drive the dispatcher's confirmation
    app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                              tiger_url=None, cors_origins=["*"], pipeline="rules"))
    with TestClient(app) as c:
        yield c


def confirm(client, iid: str, building_id) -> dict:
    """Confirm, wait for the background brief job, return the latest brief (or the error body)."""
    r = client.post(f"/api/incidents/{iid}/confirm-location", json={"building_id": building_id})
    if r.status_code != 202:
        return {"status_code": r.status_code, **r.json()}
    client.app.state.svc.wait(iid)
    return client.get(f"/api/incidents/{iid}/briefs/latest").json()


def test_health(client):
    r = client.get("/health").json()
    assert r["ok"] and r["data_mode"] == "fixture" and r["extractor"] == "keyword"


def test_incident_crud(client):
    r = client.post("/api/incidents", json={"incident_id": "inc_crud"})
    assert r.status_code == 201 and r.json()["status"] == "listening"
    assert client.post("/api/incidents", json={"incident_id": "inc_crud"}).status_code == 409
    assert [i["incident_id"] for i in client.get("/api/incidents").json()] == ["inc_crud"]
    assert client.get("/api/incidents/inc_crud").status_code == 200
    assert client.delete("/api/incidents/inc_crud").status_code == 204
    assert client.get("/api/incidents/inc_crud").status_code == 404
    assert client.get("/api/incidents/..%2Fetc").status_code in (404, 422)


def test_s2_end_to_end(client):
    client.post("/api/incidents", json={"incident_id": "inc_s2"})
    segs = load_script("S2")
    for s in segs[:4]:
        client.post("/api/incidents/inc_s2/segments", json=s.model_dump())
    # no brief before confirmation
    assert client.get("/api/incidents/inc_s2/briefs/latest").status_code == 404
    cands = client.get("/api/incidents/inc_s2/candidates").json()
    assert cands[0]["building_id"] == "wilshire_tower" and cands[0]["scan_available"]

    b1 = confirm(client, "inc_s2", "wilshire_tower")
    assert b1["version"] == 1 and b1["location_confirmed"] and b1["published"]
    for s in segs[4:]:
        client.post("/api/incidents/inc_s2/segments", json=s.model_dump())
    client.app.state.svc.wait("inc_s2")
    briefs = client.get("/api/incidents/inc_s2/briefs").json()
    latest = briefs[-1]
    assert latest["version"] > 1 and latest["size_up"]["incident.floor"]["status"] == "contradicted"
    assert any(c.startswith("! incident.floor") for b in briefs[1:] for c in b["changes_since_last"])

    rec = client.get("/api/incidents/inc_s2").json()
    assert rec["status"] == "updated" and len(rec["contradictions"]) == 1
    copilot = client.get("/api/incidents/inc_s2/copilot").json()
    assert copilot[0]["reason"] == "contradiction"

    # dispatcher resolves -> new brief without the contradiction
    client.post("/api/incidents/inc_s2/contradictions/resolve", json={"key": "incident.floor", "value": 4})
    client.app.state.svc.wait("inc_s2")
    last = client.get("/api/incidents/inc_s2/briefs/latest").json()
    assert last["size_up"]["incident.floor"]["value"] == 4
    assert not any(g.startswith("Unresolved") for g in last["gaps"])

    ttb = client.get("/api/metrics/time-to-brief").json()
    assert ttb["n"] == 1 and ttb["source"] == "workspace"


def test_s3_requires_confirmation(client):
    r = client.post("/api/calls/simulate", json={"script_id": "S3", "incident_id": "inc_s3"}).json()
    assert r["status"] == "done"
    cands = client.get("/api/incidents/inc_s3/candidates").json()
    assert len(cands) >= 2
    assert client.get("/api/incidents/inc_s3/briefs").json() == []
    assert confirm(client, "inc_s3", "nope")["status_code"] == 404
    b1 = confirm(client, "inc_s3", "harbor_plaza_pine")
    b2 = confirm(client, "inc_s3", "harbor_plaza_ocean")
    assert b2["version"] == b1["version"] + 1
    assert b2["changes_since_last"][0].startswith("~ building: harbor_plaza_pine -> harbor_plaza_ocean")


def test_simulate_with_auto_confirm_and_feedback(client):
    r = client.post("/api/calls/simulate", json={"script_id": "S4", "confirm_building_id": "maple_lofts"}).json()
    iid = r["incident_id"]
    b = client.get(f"/api/incidents/{iid}/briefs/latest", headers={"X-Role": "officer"}).json()
    assert any("No interior scan" in g for g in b["gaps"])
    fb = client.post(f"/api/incidents/{iid}/feedback", headers={"X-Role": "officer"},
                     json={"fact_key": "building.year_built", "verdict": "outdated", "note": "renovated 2019"})
    assert fb.status_code == 201 and fb.json()["building_id"] == "maple_lofts"
    actions = [a["action"] for a in client.get(f"/api/incidents/{iid}/audit").json()]
    assert "view_brief" in actions and "feedback" in actions

    # the note follows the building into the next incident's brief
    r2 = client.post("/api/calls/simulate", json={"script_id": "S4", "confirm_building_id": "maple_lofts"}).json()
    b2 = client.get(f"/api/incidents/{r2['incident_id']}/briefs/latest").json()
    assert b2["crew_notes"][0]["note"] == "renovated 2019"


def test_copilot_dismiss(client):
    client.post("/api/incidents", json={"incident_id": "inc_cp"})
    sugg = client.get("/api/incidents/inc_cp/copilot").json()
    sid = sugg[0]["id"]
    assert client.post(f"/api/copilot/suggestion/{sid}/dismiss").json()["status"] == "dismissed"
    assert sid not in [s["id"] for s in client.get("/api/incidents/inc_cp/copilot").json()]
    assert client.post("/api/copilot/suggestion/inc_cp--nope/dismiss").status_code == 404


def test_dossier_endpoint_and_admin(client):
    d = client.get("/api/buildings/wilshire_tower/dossier").json()
    assert d["scan_available"] and d["evidence"]
    assert client.get("/api/buildings/nope/dossier").status_code == 404
    assert len(client.get("/api/buildings").json()) == 4
    assert client.post("/api/admin/data-mode", json={"mode": "live"}).status_code == 403
    r = client.post("/api/admin/data-mode", json={"mode": "live"}, headers={"X-Role": "admin"})
    assert r.json()["data_mode"] == "live"
    # live adapters are pending -> fall back to fixtures, and say so
    d = client.get("/api/buildings/maple_lofts/dossier?refresh=true").json()
    assert d["building_id"] == "maple_lofts"
    assert any("costar" in m for m in client.get("/health").json()["live_fallbacks"])


def test_websocket_streams_events(client):
    client.post("/api/incidents", json={"incident_id": "inc_ws"})
    with client.websocket_connect("/ws/incidents/inc_ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "incident.updated" and snap["snapshot"]
        seg = load_script("S1")[1]
        client.post("/api/incidents/inc_ws/segments", json=seg.model_dump())
        types = [ws.receive_json()["type"] for _ in range(4)]
        assert types[:2] == ["transcript.segment", "incident.updated"]
        assert "location.candidates" in types


def test_websocket_late_joiner_gets_the_transcript_so_far(client):
    """A device that follows a Call in connects after lines were said: it catches up first."""
    client.post("/api/incidents", json={"incident_id": "inc_late"})
    segs = load_script("S1")[:2]
    for seg in segs:
        client.post("/api/incidents/inc_late/segments", json=seg.model_dump())
    with client.websocket_connect("/ws/incidents/inc_late") as ws:
        assert ws.receive_json()["type"] == "incident.updated"
        past = [ws.receive_json() for _ in segs]
        assert [e["type"] for e in past] == ["transcript.segment"] * len(segs)
        assert [e["payload"]["segment"]["text"] for e in past] == [s.text for s in segs]
        assert all(e["snapshot"] and "extracted" in e["payload"] for e in past)


def test_restart_replays_from_workspace(tmp_path):
    s = Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES, tiger_url=None, pipeline="rules")
    with TestClient(create_app(s)) as c:
        c.post("/api/calls/simulate", json={"script_id": "S2", "incident_id": "inc_r"})
        c.post("/api/incidents/inc_r/contradictions/resolve", json={"key": "incident.floor", "value": 3})
        c.app.state.svc.wait("inc_r")
        before = c.get("/api/incidents/inc_r").json()
    with TestClient(create_app(s)) as c:   # fresh process state
        after = c.get("/api/incidents/inc_r").json()
    assert after["facts"] == before["facts"] and after["contradictions"] == before["contradictions"]
