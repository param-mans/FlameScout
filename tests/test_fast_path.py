"""FAST_PIPELINE=1 (docs/FAST_PIPELINE.md), offline over the API with a fake model, on S5 (fake_cedar_house).

One structured call gives the overlay and the considerations; code resolves the places, checks both and writes
overlay_vN + brief_vN. Checks: overlay.updated then brief.updated with the same version and trace id; one retry with
the errors, then the agent path unchanged; nothing invalid is emitted; at most 2 runs per call, debounced."""
import json

import pytest
from fastapi.testclient import TestClient

from flamescout_schemas import CallSegment
from services.agents import copilot, fast_path
from services.agents import overlay as overlay_mod
from services.agents.fast_path import FastOutput
from services.api import service as service_mod
from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES, load_script

HOUSE = "fake_cedar_house"
KIDS = "c1:42.0-51.5"
SMOKE = "c1:12.0-20.5"

GOOD = {
    "fire": [{"id": "fire-1", "kind": "smoke", "confidence": 0.9, "evidence_ids": [SMOKE],
              "note": "smoke from the kitchen", "place": {"type": "kitchen", "floor_reported": 1}}],
    "occupants": [{"id": "occ-1", "count": 2, "confidence": 0.6, "evidence_ids": [KIDS],
                   "note": "two kids in the back bedroom upstairs", "place": {"type": "bedroom", "floor_reported": 2}}],
    "objectives": [{"id": "obj-1", "type": "rescue", "priority": 1, "target": {"kind": "occupant", "ref_id": "occ-1"},
                    "rationale": "Two children reported upstairs.", "evidence_ids": [KIDS]}],
    "considerations": [
        {"category": "access", "title": "Consider the front door and the hall stairs to reach upstairs",
         "rationale": "Scan: front door to the hall stairs; air: driveway on the east side.",
         "evidence_ids": ["ev_ch_scan_fp_1", "ev_ch_aerial_1"], "refs": ["occ-1"]},
        {"category": "life_safety", "title": "Consider a search of the upstairs bedrooms for two children",
         "rationale": "A caller reported two children in a bedroom upstairs.", "evidence_ids": [KIDS],
         "refs": ["occ-1", "obj-1"]}],
}
EARLY = {  # mid-call: only the smoke line so far
    "fire": GOOD["fire"],
    "considerations": [{**GOOD["considerations"][0], "refs": ["fire-1"]},
                       {"category": "hazard", "title": "Consider checking the kitchen first for the reported smoke",
                        "rationale": "A caller reported smoke coming out of the kitchen.", "evidence_ids": [SMOKE],
                        "refs": ["fire-1"]}],
}
BAD = json.loads(json.dumps(GOOD))
BAD["fire"][0]["evidence_ids"] = ["ev_made_up"]            # not a caller span
BAD["considerations"][0]["title"] = "You must use the front door"  # an order, not advisory


class FakeFast:
    """Stands in for the chat model: with_structured_output(...).invoke(messages) -> the next answer."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def with_structured_output(self, schema, **kw):
        assert schema is FastOutput
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return {"raw": None, "parsed": FastOutput.model_validate(answer), "parsing_error": None}


@pytest.fixture
def make_client(tmp_path, monkeypatch, cedar_plan_index):
    monkeypatch.setattr(service_mod, "FAST_DEBOUNCE_S", 0.3)

    def make(model, auto="0"):
        monkeypatch.setenv("AUTO_CONFIRM_LOCATION", auto)
        app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                                  tiger_url=None, cors_origins=["*"], pipeline="orchestrator"))
        app.state.svc.fast_model = model
        return TestClient(app)
    return make


def post(client, iid, segments):
    for s in segments:
        client.post(f"/api/incidents/{iid}/segments", json=s.model_dump())


def c1(client, iid="inc_f"):
    client.post("/api/incidents", json={"incident_id": iid})
    post(client, iid, [s for s in load_script("S5") if s.call_id == "c1"])
    client.app.state.svc.wait(iid)


def history(client, iid="inc_f"):
    return client.app.state.bus.history(iid)


def assert_overlays_valid(client, iid="inc_f"):
    index = overlay_mod.load_plan_index(HOUSE)
    for e in history(client, iid):
        if e.type == "overlay.updated":
            assert overlay_mod.validate_overlay.validate(e.payload["overlay"], index) == []


def test_fast_path_emits_overlay_then_brief_with_the_same_version(make_client, cedar_plan_index):
    model = FakeFast(GOOD)
    with make_client(model) as client:
        svc = client.app.state.svc
        assert svc.fast_enabled()
        c1(client)
        assert not [e for e in history(client) if e.type == "dossier.progress" and
                    e.payload.get("status") == "awaiting_confirmation"]  # no agent started before the tap
        r = client.post("/api/incidents/inc_f/confirm-location", json={"building_id": HOUSE})
        assert r.status_code == 202
        svc.wait("inc_f")

        assert len(model.calls) == 1
        prompt = model.calls[0][1].content
        assert KIDS in prompt and "bedroom" in prompt and "[ev_ch_scan_fp_1]" in prompt and "over: kitchen" in prompt
        hist = history(client)
        types = [e.type for e in hist]
        i_ov, i_br = types.index("overlay.updated"), types.index("brief.updated")
        assert i_br == i_ov + 1 and types[i_br + 1] == "incident.updated" and "metric.tick" in types[i_br:]
        ov, br = hist[i_ov], hist[i_br]
        assert ov.trace_id == br.trace_id and ov.payload["version"] == br.payload["version"] == 1
        assert br.payload["pipeline"] == "fast" and br.payload["brief"]["published"]
        assert hist[i_br + 1].payload["incident"]["status"] == "confirmed"

        overlay = ov.payload["overlay"]
        assert overlay_mod.validate_overlay.validate(overlay, cedar_plan_index) == []
        assert overlay["fire"][0]["location"] == {"kind": "room", "room_id": "r4"}     # resolved by code
        assert overlay["fire"][0]["confidence"] == 0.6                                  # caller cap
        assert overlay["occupants"][0]["location"] == {"kind": "floor", "floor_reported": 2, "floor_id": "f2"}
        assert overlay["routes"] == []
        brief = br.payload["brief"]
        assert [c["refs"] for c in brief["considerations"]] == [["occ-1"], ["occ-1", "obj-1"]]
        assert brief["considerations"][0]["cross_layer"] and KIDS in brief["evidence"]

        # same files as the agent path: the latest endpoints, and the dispatcher question for the ambiguous bedroom
        assert client.get("/api/incidents/inc_f/overlay/latest").json()["version"] == 1
        assert client.get("/api/incidents/inc_f/briefs/latest").json()["version"] == 1
        asked = [q for q in copilot.read_questions(svc._agents_ws("inc_f")) if q.reason == "ambiguous_place"]
        assert [q.room_ids for q in asked] == [["r10", "r9"]]


def test_a_failed_check_is_retried_once_with_the_errors(make_client):
    model = FakeFast(BAD, GOOD)
    with make_client(model) as client:
        c1(client)
        client.post("/api/incidents/inc_f/confirm-location", json={"building_id": HOUSE})
        client.app.state.svc.wait("inc_f")
        assert len(model.calls) == 2
        retry = model.calls[1][-1].content
        assert "rejected" in retry and "ev_made_up" in retry and "order language" in retry
        [ov] = [e for e in history(client) if e.type == "overlay.updated"]
        assert ov.payload["overlay"]["fire"][0]["evidence_ids"] == [SMOKE]
        assert [e.payload["pipeline"] for e in history(client) if e.type == "brief.updated"] == ["fast"]


@pytest.mark.parametrize("second", [BAD, RuntimeError("429 RESOURCE_EXHAUSTED")])
def test_two_failures_fall_back_to_the_agent_path_and_emit_nothing_invalid(make_client, second):
    model = FakeFast(BAD, second)
    with make_client(model) as client:
        svc = client.app.state.svc
        c1(client)
        client.post("/api/incidents/inc_f/confirm-location", json={"building_id": HOUSE})
        svc.wait("inc_f")
        assert len(model.calls) == 2
        hist = history(client)
        briefs = [e.payload for e in hist if e.type == "brief.updated"]
        # the agent path, unchanged (offline: no agent model, so its facts-only brief and empty overlay)
        assert [b["pipeline"] for b in briefs] == ["orchestrator"] and briefs[0]["version"] == 1
        overlays = [e.payload["overlay"] for e in hist if e.type == "overlay.updated"]
        assert len(overlays) == 1 and all("ev_made_up" not in json.dumps(o) for o in overlays)
        assert_overlays_valid(client)
        types = [e.type for e in hist]
        assert types.index("brief.updated") + 1 == types.index("overlay.updated")  # the agent path's order

        # later changes stay on the agent path: no more fast runs
        post(client, "inc_f", [s for s in load_script("S5") if s.call_id == "c2"])
        svc.wait("inc_f")
        assert len(model.calls) == 2
        assert {e.payload["pipeline"] for e in history(client) if e.type == "brief.updated"} == {"orchestrator"}


def extra(call_id, t, text):
    return CallSegment(call_id=call_id, speaker="caller", text=text, t_start=t, t_end=t + 3)


def test_at_most_two_runs_per_call_debounced_and_the_last_one_wins(make_client):
    model = FakeFast(EARLY, GOOD)
    with make_client(model, auto="1") as client:
        svc = client.app.state.svc
        client.post("/api/incidents", json={"incident_id": "inc_f"})
        segs = [s for s in load_script("S5") if s.call_id == "c1"]
        post(client, "inc_f", segs[:2])          # the address: auto-confirmed, run 1 at once (mid-call)
        svc.wait("inc_f")
        assert client.get("/api/incidents/inc_f").json()["confirmed_by"] == "auto"
        assert len(model.calls) == 1
        # two changes back to back: one debounced run that reads both (the end of the call)
        post(client, "inc_f", [segs[3], extra("c1", 60.0, "I smell gas near the garage.")])
        svc.wait("inc_f")
        assert len(model.calls) == 2
        last_prompt = model.calls[1][1].content
        assert KIDS in last_prompt and "gas near the garage" in last_prompt
        # call 1 has had its two runs: a further change in it starts none
        post(client, "inc_f", [extra("c1", 70.0, "There's also smoke upstairs now, on the second floor.")])
        svc.wait("inc_f")
        assert len(model.calls) == 2
        hist = history(client)
        assert [e.payload["version"] for e in hist if e.type == "overlay.updated"] == [1, 2]
        assert [e.payload["version"] for e in hist if e.type == "brief.updated"] == [1, 2]
        types = [e.type for e in hist]
        for n, t in enumerate(types):
            if t == "overlay.updated":
                assert types[n + 1] == "brief.updated" and hist[n].payload["version"] == hist[n + 1].payload["version"]
        assert_overlays_valid(client)
        # a new call has its own budget
        post(client, "inc_f", [s for s in load_script("S5") if s.call_id == "c2"][1:2])
        svc.wait("inc_f")
        assert len(model.calls) == 3


def test_an_older_run_finishing_after_a_newer_one_is_dropped(make_client, monkeypatch):
    model = FakeFast(GOOD)
    with make_client(model) as client:
        svc = client.app.state.svc
        c1(client)
        client.post("/api/incidents/inc_f/confirm-location", json={"building_id": HOUSE})
        svc.wait("inc_f")
        st = svc._fast["inc_f"]
        st.emitted = 5   # pretend run 5 was emitted; run 2 (older) must not be
        svc._fast_run("inc_f", st, 2)
        assert [e.payload["version"] for e in history(client) if e.type == "overlay.updated"] == [1]


def test_fast_path_is_off_without_a_model_or_with_fast_pipeline_0(tmp_path, monkeypatch):
    s = Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES, tiger_url=None,
                 pipeline="orchestrator")
    assert s.fast_pipeline and s.overlay_model is None   # conftest cleared any .env value
    assert not fast_path.available()                     # no GEMINI_API_KEY offline
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_FAST_MODEL", "fast-model")
    assert fast_path.available() and fast_path.overlay_model_name() == "fast-model"
    monkeypatch.setenv("GEMINI_OVERLAY_MODEL", "overlay-model")
    assert fast_path.overlay_model_name() == "overlay-model"
    assert Settings(pipeline="orchestrator").overlay_model == "overlay-model"
    monkeypatch.setenv("FAST_PIPELINE", "0")
    assert not Settings(pipeline="orchestrator").fast_pipeline
