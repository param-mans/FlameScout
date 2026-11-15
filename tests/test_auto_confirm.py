"""AUTO_CONFIRM_LOCATION (docs/AUTO_CONFIRM.md) and the call-script manifest, offline.

Auto-confirmation goes through IncidentService.confirm_location, the endpoint's path, so the events are the
same as a dispatcher's tap; only confirmed_by differs ("auto" vs "dispatcher")."""
import json

import pytest
from fastapi.testclient import TestClient

from flamescout_schemas import LocationCandidate
from services.api import main
from services.api.config import Settings
from services.api.main import create_app
from services.api.service import auto_confirm_pick

from .conftest import FIXTURES, load_script

BUILDING = "fake_wilshire_tower"


def make_client(tmp_path, monkeypatch, pipeline: str, auto: str):
    monkeypatch.setenv("AUTO_CONFIRM_LOCATION", auto)
    return TestClient(create_app(Settings(data_mode="fixture", workspace=tmp_path / f"ws_{pipeline}_{auto}",
                                          fixtures=FIXTURES, tiger_url=None, cors_origins=["*"],
                                          pipeline=pipeline)))


def post_call(client, iid: str, script: str, call_id: str) -> None:
    for s in load_script(script):
        if s.call_id == call_id:
            client.post(f"/api/incidents/{iid}/segments", json=s.model_dump())
    client.app.state.svc.wait(iid)


def types_after_candidates(client, iid: str) -> list[str]:
    hist = client.app.state.bus.history(iid)
    first = next(n for n, e in enumerate(hist) if e.type == "location.candidates")
    return [e.type for e in hist[first:] if e.type not in ("transcript.segment", "copilot.suggestion",
                                                            "location.candidates", "dossier.progress")]


def cand(bid: str, score: float) -> LocationCandidate:
    return LocationCandidate(building_id=bid, address="x", score=score)


def test_auto_confirm_pick():
    assert auto_confirm_pick([]) is None
    assert auto_confirm_pick([cand("a", 0.3)]) == "a"                       # a single candidate
    assert auto_confirm_pick([cand("b", 0.5), cand("a", 0.9)]) == "a"       # best first, whatever the order
    assert auto_confirm_pick([cand("a", 0.8), cand("b", 0.6)]) == "a"       # margin exactly 0.2
    assert auto_confirm_pick([cand("a", 0.79), cand("b", 0.1)]) is None     # score too low
    assert auto_confirm_pick([cand("a", 1.0), cand("b", 0.85)]) is None     # margin too small


def test_auto_confirms_a_clear_candidate_like_the_dispatcher(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, "orchestrator", "1") as c:
        c.post("/api/incidents", json={"incident_id": "inc_auto"})
        n = 0
        for n, s in enumerate(load_script("S2"), 1):   # up to the segment that locates; no confirm-location call
            out = c.post("/api/incidents/inc_auto/segments", json=s.model_dump()).json()
            if out["incident"]["confirmed_building_id"]:
                break
        c.app.state.svc.wait("inc_auto")
        rec = c.get("/api/incidents/inc_auto").json()
        assert rec["confirmed_building_id"] == BUILDING and rec["confirmed_by"] == "auto"
        assert rec["status"] in ("confirmed", "updated")
        b1 = c.get("/api/incidents/inc_auto/briefs/latest").json()
        assert (b1["version"], b1["building_id"]) == (1, BUILDING)
        hist = c.app.state.bus.history("inc_auto")
        briefing = [e.payload["incident"] for e in hist if e.type == "incident.updated"
                    and e.payload["incident"]["status"] == "briefing"]
        assert briefing and briefing[0]["confirmed_by"] == "auto"
        i = next(n for n, e in enumerate(hist) if e.type == "brief.updated")
        assert hist[i + 1].type == "overlay.updated"
        assert hist[i + 1].payload["version"] == hist[i].payload["version"] == 1
        audit = [a for a in c.get("/api/incidents/inc_auto/audit").json() if a["action"] == "confirm_location"]
        assert audit == [{**audit[0], "role": "system", "detail": {"building_id": BUILDING, "confirmed_by": "auto"}}]
        auto_types = types_after_candidates(c, "inc_auto")
        ov = c.get("/api/incidents/inc_auto/overlay/latest").json()   # what a late client missed
        assert ov == hist[i + 1].payload
        assert c.get("/api/incidents").json()[0]["confirmed_by"] == "auto"

    with make_client(tmp_path, monkeypatch, "orchestrator", "0") as c:   # the same call, confirmed by hand
        c.post("/api/incidents", json={"incident_id": "inc_auto"})
        for s in load_script("S2")[:n]:
            c.post("/api/incidents/inc_auto/segments", json=s.model_dump())
        c.app.state.svc.wait("inc_auto")
        assert c.get("/api/incidents/inc_auto").json()["confirmed_by"] is None
        r = c.post("/api/incidents/inc_auto/confirm-location", json={"building_id": BUILDING})
        assert r.json()["confirmed_by"] == "dispatcher"
        c.app.state.svc.wait("inc_auto")
        assert c.get("/api/incidents/inc_auto").json()["confirmed_by"] == "dispatcher"
        assert types_after_candidates(c, "inc_auto") == auto_types


def test_rules_pipeline_auto_confirms_and_simulate_does_not_confirm_twice(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, "rules", "1") as c:
        r = c.post("/api/calls/simulate", json={"script_id": "S4", "confirm_building_id": "maple_lofts"}).json()
        rec = r["incident"]
        assert rec["confirmed_building_id"] == "maple_lofts"
        assert c.get(f"/api/incidents/{r['incident_id']}").json()["confirmed_by"] == "auto"
        assert [b["version"] for b in c.get(f"/api/incidents/{r['incident_id']}/briefs").json()][0] == 1
        actions = [a for a in c.get(f"/api/incidents/{r['incident_id']}/audit").json()
                   if a["action"] == "confirm_location"]
        assert len(actions) == 1


def test_ambiguity_waits_for_the_dispatcher(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, "rules", "1") as c:
        c.post("/api/calls/simulate", json={"script_id": "S3", "incident_id": "inc_s3"})
        assert len(c.get("/api/incidents/inc_s3/candidates").json()) >= 2
        rec = c.get("/api/incidents/inc_s3").json()
        assert rec["confirmed_building_id"] is None and rec["confirmed_by"] is None
        assert rec["status"] == "locating"
        assert c.get("/api/incidents/inc_s3/overlay/latest").status_code == 404
        assert c.get("/api/incidents/inc_s3/briefs").json() == []
        c.post("/api/incidents/inc_s3/confirm-location", json={"building_id": "harbor_plaza_pine"})
        c.app.state.svc.wait("inc_s3")
        assert c.get("/api/incidents/inc_s3").json()["confirmed_by"] == "dispatcher"


def test_auto_confirm_off_keeps_the_pause(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, "orchestrator", "0") as c:
        c.post("/api/incidents", json={"incident_id": "inc_off"})
        post_call(c, "inc_off", "S2", "c1")
        rec = c.get("/api/incidents/inc_off").json()
        assert rec["confirmed_building_id"] is None and rec["status"] == "locating"
        assert c.get("/api/incidents/inc_off/briefs/latest").status_code == 404


# ---------------- scripts + manifest ----------------

def _script(d, sid: str, desc: str) -> None:
    (d / f"{sid.lower()}.json").write_text(json.dumps({"script_id": sid, "description": desc, "segments": [
        {"call_id": "c1", "speaker": "caller", "t_start": 0.0, "t_end": 1.0, "text": "hello"}]}))


@pytest.fixture
def calls_dir(tmp_path, monkeypatch):
    d = tmp_path / "calls"
    d.mkdir()
    for sid in ("T1", "T2", "SHOW1", "SHOW2", "HIDDEN", "ORPHAN"):
        _script(d, sid, f"{sid} description")
    monkeypatch.setattr(main, "CALLS_DIR", d)
    return d


def test_scripts_without_manifest_lists_every_script(tmp_path, monkeypatch, calls_dir):
    with make_client(tmp_path, monkeypatch, "rules", "1") as c:
        ids = [s["script_id"] for s in c.get("/api/calls/scripts").json()]
    assert sorted(ids) == ["HIDDEN", "ORPHAN", "SHOW1", "SHOW2", "T1", "T2"]


def test_scripts_follow_the_manifest(tmp_path, monkeypatch, calls_dir):
    (calls_dir / "manifest.json").write_text(json.dumps({"scripts": [
        {"id": "T2", "title": "Test two", "group": "test", "house": "x", "inconsistent": True, "visible": True},
        {"id": "SHOW2", "title": "Show two", "group": "showcase", "house": "209217448", "inconsistent": False,
         "visible": True},
        {"id": "HIDDEN", "title": "Hidden", "group": "showcase", "house": "x", "inconsistent": False,
         "visible": False},
        {"id": "T1", "title": "Test one", "group": "test", "house": "y", "inconsistent": False, "visible": True},
        {"id": "SHOW1", "title": "Show one", "group": "showcase", "house": "214905148", "inconsistent": False,
         "visible": True},
        {"id": "MISSING", "title": "No file", "group": "test", "house": "z", "inconsistent": False,
         "visible": True}]}))
    with make_client(tmp_path, monkeypatch, "rules", "1") as c:
        out = c.get("/api/calls/scripts").json()
        assert [s["script_id"] for s in out] == ["SHOW2", "SHOW1", "T2", "T1"]
        assert out[0] == {"script_id": "SHOW2", "description": "SHOW2 description", "segments": 1,
                          "title": "Show two", "group": "showcase", "inconsistent": False, "house": "209217448"}
        assert out[2]["inconsistent"] is True
        # hidden and unlisted scripts still load by id; the manifest is never a script
        for sid in ("HIDDEN", "ORPHAN"):
            assert c.post("/api/calls/simulate", json={"script_id": sid}).status_code == 202
        assert c.post("/api/calls/simulate", json={"script_id": "manifest"}).status_code == 404


def test_real_calls_dir_never_lists_the_manifest(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, "rules", "0") as c:
        out = c.get("/api/calls/scripts").json()
        assert out and all(s["script_id"].lower() != "manifest" for s in out)
        assert c.post("/api/calls/simulate", json={"script_id": "manifest"}).status_code == 404
