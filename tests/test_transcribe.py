"""Call-in monologue: one recording -> Gemini sentences -> caller segments; /ws/calls follows it. Offline."""
import io
import time
import wave

import pytest
from fastapi.testclient import TestClient

from services.api import transcribe
from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES


def make_wav(seconds: float = 4.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def test_duration_and_bad_wav():
    assert transcribe.duration_s(make_wav(2.5)) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        transcribe.duration_s(b"not a wav")


def test_timed_spreads_sentences_by_words_in_order():
    out = transcribe.timed(["one two", "three four five six"], 6.0)
    assert out == [("one two", 0.0, 2.0), ("three four five six", 2.0, 6.0)]
    assert transcribe.timed([], 5.0) == []


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                              tiger_url=None, cors_origins=["*"], pipeline="rules"))
    with TestClient(app) as c:
        yield c


def upload(client, call, body=None):
    return client.post("/api/calls/web/callin/audio", content=make_wav() if body is None else body,
                       params={"incident_id": call["incident_id"], "call_label": call["call_label"]})


def settled(client, call, timeout=5.0):
    """Poll the call's status until the background transcription is over (as the web app does)."""
    params = {"incident_id": call["incident_id"], "call_label": call["call_label"]}
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        c = client.get("/api/calls/web/callin/status", params=params).json()["call"]
        if c["status"] != "transcribing":
            return c
        time.sleep(0.02)
    raise AssertionError("still transcribing")


def test_callin_is_announced_to_every_open_app(client):
    with client.websocket_connect("/ws/calls") as feed:
        call = client.post("/api/calls/web/callin").json()
        ev = feed.receive_json()
        assert ev["type"] == "call.status"
        assert ev["payload"]["call"]["incident_id"] == call["incident_id"]
        assert ev["payload"]["call"]["status"] == "connected"
    with client.websocket_connect("/ws/calls") as late:        # opened mid-call: told about it first
        snap = late.receive_json()
        assert snap["snapshot"] and snap["payload"]["call"]["call_id"] == call["call_label"]


def test_monologue_is_transcribed_in_one_go_and_ends_the_call(client, monkeypatch):
    said = ["There's a fire at 1200 Wilshire Boulevard.", "Smoke on the third floor.", "My neighbor is trapped."]
    monkeypatch.setattr(transcribe, "transcribe_monologue", lambda wav: said)
    call = client.post("/api/calls/web/callin").json()
    with client.websocket_connect("/ws/calls") as feed:
        feed.receive_json()                                    # snapshot: the active call
        r = upload(client, call)
        statuses = [feed.receive_json()["payload"]["call"]["status"] for _ in range(2)]
    assert r.status_code == 202 and r.json()["seconds"] == 4.0          # answered before transcribing
    assert statuses == ["transcribing", "completed"]
    done = settled(client, call)
    assert done["status"] == "completed" and done["recorded"] == 3
    inc = client.get(f"/api/incidents/{call['incident_id']}").json()
    assert inc["facts"]["location.address"]["value"] == "1200 Wilshire Boulevard"
    assert inc["facts"]["incident.floor"]["value"] == 3 and inc["facts"]["people.trapped"]["value"] is True
    copilot = client.get(f"/api/incidents/{call['incident_id']}/copilot").json()
    assert all(s["status"] == "open" for s in copilot)       # nothing handed out: nobody is answering
    assert upload(client, call).status_code == 404             # the call is over


def test_failed_transcription_keeps_the_call_open_for_a_retry(client, monkeypatch):
    call = client.post("/api/calls/web/callin").json()

    def boom(wav):
        raise RuntimeError("quota")
    monkeypatch.setattr(transcribe, "transcribe_monologue", boom)
    assert upload(client, call).status_code == 202
    assert settled(client, call)["status"] == "transcription_failed"      # still open: retry
    monkeypatch.setattr(transcribe, "transcribe_monologue", lambda wav: ["Smoke on the third floor."])
    assert upload(client, call).status_code == 202
    assert settled(client, call)["recorded"] == 1


def test_second_upload_while_transcribing_is_409(client, monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr(transcribe, "transcribe_monologue", lambda wav: gate.wait(5) and ["Fire."])
    call = client.post("/api/calls/web/callin").json()
    assert upload(client, call).status_code == 202
    assert upload(client, call).status_code == 409
    gate.set()
    assert settled(client, call)["status"] == "completed"


def test_bad_audio_is_rejected(client, monkeypatch):
    call = client.post("/api/calls/web/callin").json()
    assert upload(client, call, body=b"garbage").status_code == 422
    monkeypatch.setattr(transcribe, "MAX_WAV_BYTES", 100)
    assert upload(client, call).status_code == 413


def test_registry_second_call_in_window_joins_the_incident_as_c2():
    from services.api.calls import CallRegistry

    now = [1000.0]
    made = iter(["inc_a", "inc_b"])
    reg = CallRegistry(lambda: next(made), lambda i: True, window_s=600, clock=lambda: now[0])
    c1 = reg.start()
    reg.end(c1)
    now[0] += 300
    c2 = reg.start()
    assert (c1.incident_id, c1.call_id, c2.incident_id, c2.call_id) == ("inc_a", "c1", "inc_a", "c2")
    reg.end(c2)
    now[0] += 601                                   # window passed: a new fire, a new incident
    c3 = reg.start()
    assert (c3.incident_id, c3.call_id) == ("inc_b", "c1")
    assert [c.call_id for c in reg.calls(active_only=True)] == ["c1"]


def test_clear_cancels_open_calls_and_next_call_starts_a_new_incident(client):
    first = client.post("/api/calls/web/callin").json()
    with client.websocket_connect("/ws/calls") as ws:
        ws.receive_json()                                    # snapshot: the open call
        r = client.post("/api/calls/web/clear")
        assert ws.receive_json()["payload"]["call"]["status"] == "canceled"
        assert ws.receive_json()["type"] == "calls.cleared"
    assert r.status_code == 200
    assert [c["status"] for c in r.json()["canceled"]] == ["canceled"]
    assert upload(client, first).status_code == 404          # the cleared call takes no recording
    second = client.post("/api/calls/web/callin").json()
    assert second["incident_id"] != first["incident_id"] and second["call_label"] == "c1"
    third = client.post("/api/calls/web/callin").json()      # without a clear, callers still join
    assert third["incident_id"] == second["incident_id"] and third["call_label"] == "c2"


def test_split_sentences():
    assert transcribe.split_sentences(" Smoke on 3. Kids inside!  Hurry? ok") == \
        ["Smoke on 3.", "Kids inside!", "Hurry?", "ok"]
    assert transcribe.split_sentences("   ") == []


def test_primary_then_fallback_provider(monkeypatch):
    for k in ("GEMINI_API_KEY", "GEMINI_FAST_MODEL", "XAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "xai")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_FAST_MODEL", "m")
    monkeypatch.setenv("XAI_API_KEY", "x")
    assert transcribe.providers() == ["gemini", "xai"]
    tried = []

    def out_of_credits(wav):
        tried.append("gemini")
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setitem(transcribe._BACKENDS, "gemini", out_of_credits)
    monkeypatch.setitem(transcribe._BACKENDS, "xai", lambda wav: tried.append("xai") or ["Fire."])
    assert transcribe.transcribe_monologue(b"") == ["Fire."] and tried == ["gemini", "xai"]

    monkeypatch.setenv("LLM_PROVIDER", "openai")     # no OPENAI key: skipped
    assert transcribe.providers() == ["xai"]
    monkeypatch.delenv("XAI_API_KEY")
    with pytest.raises(RuntimeError):
        transcribe.transcribe_monologue(b"")
