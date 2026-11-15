"""Caller camera: frame reading -> new observations -> camera segments in fusion. Offline (vision mocked)."""
import pytest
from fastapi.testclient import TestClient

from flamescout_schemas import CallSegment
from services.agents import camera
from services.agents.camera import CameraState, FrameReading, observe
from services.agents.extract import ExtractedFact
from services.agents.fusion import CAMERA_CONF, FusionEngine
from services.api import main as api_main
from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64


def reading(**kw) -> FrameReading:
    return FrameReading(**{"usable": True, **kw})


def test_observe_reports_only_what_is_new():
    st = CameraState()
    text, facts = observe(reading(room="kitchen", room_confidence=0.9, fire="smoke", hazards=["propane"],
                                  summary="A kitchen with smoke near the stove."), st)
    assert {(f.key, f.value) for f in facts} == {("caller.room", "kitchen"), ("incident.visible", "smoke"),
                                                ("hazards.reported", "propane")}
    assert all(f.quote in text for f in facts)               # grounded in the segment text by construction
    assert observe(reading(room="kitchen", room_confidence=0.9, fire="smoke", hazards=["propane"]), st) is None
    text, facts = observe(reading(room="kitchen", room_confidence=0.9, fire="flames"), st)
    assert [(f.key, f.value) for f in facts] == [("incident.visible", "flames")]
    assert observe(reading(fire="smoke"), st) is None        # smoke after flames is not news


def test_observe_drops_unsure_room_and_unusable_frames():
    st = CameraState()
    assert observe(reading(room="bedroom", room_confidence=0.3), st) is None
    assert observe(FrameReading(usable=False, fire="flames"), st) is None
    _, facts = observe(reading(room="outside", room_confidence=0.9, people_visible=True), st)
    assert [(f.key, f.value) for f in facts] == [("caller.room", "outside")]   # people outside aren't occupants


class NoExtract:
    def extract(self, text):
        return [ExtractedFact(key="incident.visible", value="smoke", quote="smoke")] if "smoke" in text else []


def cam_seg(text="Smoke visible.", t=1.0):
    return CallSegment(call_id="cam1", speaker="camera", text=text, t_start=t, t_end=t)


def test_fusion_camera_fact_is_inferred_until_a_caller_says_it():
    eng = FusionEngine("i1", NoExtract())
    eng.ingest(cam_seg(), [ExtractedFact(key="incident.visible", value="smoke", quote="Smoke visible")],
               frame_uri="/api/incidents/i1/camera/frames/cam1-0001.jpg")
    f = eng.record.facts["incident.visible"]
    assert (f.status, f.confidence) == ("inferred", CAMERA_CONF)
    ev = eng.record.evidence[f.evidence_ids[0]]
    assert (ev.kind, ev.source, ev.uri) == ("camera_frame", "camera", "/api/incidents/i1/camera/frames/cam1-0001.jpg")
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text="there's smoke", t_start=2, t_end=3))
    assert eng.record.facts["incident.visible"].status == "reported"
    assert "cam1" in eng.record.call_ids


def test_fusion_camera_contradicts_a_caller_like_another_call():
    eng = FusionEngine("i1", NoExtract())
    eng.ingest(CallSegment(call_id="c1", speaker="caller", text="I'm in the bedroom", t_start=0, t_end=1),
               [ExtractedFact(key="caller.room", value="bedroom", quote="bedroom")])
    eng.ingest(cam_seg("Camera is in the kitchen."), [ExtractedFact(key="caller.room", value="kitchen",
                                                                    quote="Camera is in the kitchen")])
    assert eng.record.facts["caller.room"].status == "contradicted"
    assert [v.call_id for v in eng.record.contradictions[0].values] == ["c1", "cam1"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "llm_chat_providers", lambda kind: ["gemini"])
    monkeypatch.delenv("SITE_ADDRESS", raising=False)   # links follow the request's Origin unless a test sets it
    app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                              tiger_url=None, cors_origins=["*"], pipeline="rules"))
    with TestClient(app) as c:
        yield c


def test_camera_frames_become_segments_and_survive_a_reload(client, monkeypatch):
    readings = iter([reading(room="garage", room_confidence=0.9, hazards=["gasoline"]),
                     reading(room="garage", room_confidence=0.9, hazards=["gasoline"])])
    monkeypatch.setattr(camera, "read_frame", lambda jpeg: next(readings))
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    cam = client.post(f"/api/incidents/{iid}/camera").json()["camera"]
    assert cam["call_id"] == "cam1"

    r = client.post(f"/api/incidents/{iid}/camera/cam1/frames", content=JPEG).json()
    assert r["observation"].startswith("Camera is in the garage.")
    assert {x["key"] for x in r["extracted"]} == {"caller.room", "hazards.reported"}
    again = client.post(f"/api/incidents/{iid}/camera/cam1/frames", content=JPEG).json()
    assert again["observation"] is None and again["camera"]["frames"] == 2

    rec = client.get(f"/api/incidents/{iid}").json()
    uri = rec["evidence"][rec["facts"]["caller.room"]["evidence_ids"][0]]["uri"]
    assert client.get(uri).content == JPEG

    svc = client.app.state.svc
    svc._engines.clear()                                     # replay from inputs.jsonl, no vision call
    rec2 = client.get(f"/api/incidents/{iid}").json()
    assert rec2["facts"]["hazards.reported"]["value"] == ["gasoline"]
    assert rec2["facts"]["caller.room"]["status"] == "inferred"


def test_camera_rejects_bad_frames_and_ended_cameras(client):
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    client.post(f"/api/incidents/{iid}/camera")
    assert client.post(f"/api/incidents/{iid}/camera/cam1/frames", content=b"png?").status_code == 422
    client.post("/api/calls/web/clear")
    assert client.post(f"/api/incidents/{iid}/camera/cam1/frames", content=JPEG).status_code == 404
    assert client.post("/api/incidents/nope/camera").status_code == 404


def test_camera_needs_a_vision_model(client, monkeypatch):
    monkeypatch.setattr(api_main, "llm_chat_providers", lambda kind: [])
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    assert client.post(f"/api/incidents/{iid}/camera").status_code == 503


class FakeMessenger:
    """Stands in for services/messenger (POST /send {to, text} -> {ok, line})."""

    def __init__(self):
        self.sent, self.ok = [], True

    def post(self, url, json=None, timeout=None):
        self.sent.append((url, json))

        class R:
            status_code = 200 if self.ok else 502

            @staticmethod
            def json():
                return {"ok": True, "line": "+14155550000"} if self.ok else {"ok": False, "error": "NotFound: no line"}
        return R()


@pytest.fixture
def photon(monkeypatch):
    monkeypatch.setenv("PHOTON_PROJECT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "secret")
    monkeypatch.setenv("MESSENGER_URL", "http://messenger:8090")
    fake = FakeMessenger()
    monkeypatch.setattr("httpx.post", fake.post)
    return fake


def test_normalize_phone_numbers():
    from services.api import messaging
    assert messaging.normalize("(609) 555-0123") == "+16095550123"
    assert messaging.normalize("+44 20 7946 0958") == "+442079460958"
    assert messaging.normalize("911") is None


def test_invite_sends_the_link_by_imessage_and_the_phone_opens_it(client, photon):
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    r = client.post(f"/api/incidents/{iid}/camera/invite", json={"phone": "609 555 0123"},
                    headers={"origin": "https://demo.example"}).json()
    assert r["message"]["state"] == "sent" and r["camera"]["sent_to"] == "…0123"
    assert r["message"]["line"] == "+14155550000"
    assert r["url"].startswith("https://demo.example/?camera=") and "token" not in r["camera"]
    url, body = photon.sent[0]
    assert url == "http://messenger:8090/send" and body["to"] == "+16095550123" and r["url"] in body["text"]

    token = r["url"].split("=", 1)[1]
    cam = client.get(f"/api/camera/{token}").json()["camera"]
    assert (cam["incident_id"], cam["call_id"], cam["status"]) == (iid, "cam1", "streaming")
    client.post(f"/api/incidents/{iid}/camera/cam1/end")
    assert client.get(f"/api/camera/{token}").status_code == 404


def test_a_failed_send_still_gives_the_dispatcher_the_link(client, photon):
    photon.ok = False
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    r = client.post(f"/api/incidents/{iid}/camera/invite", json={"phone": "6095550123"}).json()
    assert r["message"]["state"] == "failed" and "no line" in r["message"]["detail"] and "?camera=" in r["url"]


def test_invite_without_photon_still_returns_the_link(client, monkeypatch):
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    r = client.post(f"/api/incidents/{iid}/camera/invite", json={"phone": "6095550123"}).json()
    assert r["message"]["state"] == "not_configured" and "?camera=" in r["url"]
    assert client.post(f"/api/incidents/{iid}/camera/invite", json={"phone": "12"}).status_code == 422


@pytest.mark.parametrize("site,origin,expected", [
    ("1-2-3-4.sslip.io", "https://x.ngrok-free.app", "https://1-2-3-4.sslip.io/?camera="),
    ("http://lan.example:8080", None, "http://lan.example:8080/?camera="),
    (":80", "https://x.ngrok-free.app", "https://x.ngrok-free.app/?camera="),
    ("", "https://x.ngrok-free.app", "https://x.ngrok-free.app/?camera="),
])
def test_camera_link_uses_site_address(client, monkeypatch, site, origin, expected):
    monkeypatch.setenv("SITE_ADDRESS", site)
    iid = client.post("/api/incidents", json={}).json()["incident_id"]
    r = client.post(f"/api/incidents/{iid}/camera/invite", json={}, headers={"origin": origin} if origin else {})
    assert r.json()["url"].startswith(expected)


def _wait_for(pred, timeout=3.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_call_in_texts_the_caller_the_camera_link_once(client, photon, monkeypatch):
    monkeypatch.setenv("DEMO_CALLER_PHONE", "609-555-0123")
    cams = client.app.state.cameras
    c1 = client.post("/api/calls/web/callin", headers={"origin": "https://x.ngrok-free.app"}).json()
    assert _wait_for(lambda: any((c.invite or {}).get("state") == "sent" for c in cams.active_in(c1["incident_id"])))
    assert len(photon.sent) == 1 and "https://x.ngrok-free.app/?camera=" in photon.sent[0][1]["text"]
    c2 = client.post("/api/calls/web/callin").json()                 # second caller, same incident
    assert c2["incident_id"] == c1["incident_id"]
    assert not _wait_for(lambda: len(cams.active_in(c1["incident_id"])) > 1, timeout=0.3)


def test_no_automatic_text_without_a_caller_number(client, photon):
    c1 = client.post("/api/calls/web/callin").json()
    assert not _wait_for(lambda: client.app.state.cameras.active_in(c1["incident_id"]), timeout=0.3)
    assert photon.sent == []
