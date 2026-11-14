import json
import os
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from flamescout_schemas import Brief, BuildingDossier, CallSegment, Progress
from services.agents import orchestrator
from services.agents.fusion import FusionEngine
from services.agents.orchestrator import (MAX_SUBMISSIONS, Session, make_tools, resume_incident,
                                          start_incident, update_incident)

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
BUILDING = "fake_wilshire_tower"
GOOD = {"category": "access", "title": "Consider Stair B from the rear alley",
        "rationale": "A caller reported someone shouting from the back stairs.",
        "evidence_ids": ["ev_scan_fp_4"]}


class Incident:
    """S2 replayed call by call into a tmp workspace, like scripts/run_incident.py."""

    def __init__(self, root: Path):
        self.root = root
        self.ws = root / "inc_s2"
        self.ws.mkdir()
        self.segments = [CallSegment(**s) for s in json.loads((CALLS / "s2.json").read_text())["segments"]]
        self.eng = FusionEngine("inc_s2")

    def replay(self, call_id: str) -> list[str]:
        notes = [n for s in self.segments if s.call_id == call_id for n in self.eng.ingest(s)]
        (self.ws / "incident.json").write_text(self.eng.record.model_dump_json(indent=2))
        return notes

    def confirm(self) -> None:
        self.eng.confirm(BUILDING)
        (self.ws / "incident.json").write_text(self.eng.record.model_dump_json(indent=2))


@pytest.fixture
def incident(tmp_path):
    inc = Incident(tmp_path)
    inc.replay("c1")
    return inc


@pytest.fixture
def session(incident):
    return Session(incident_id="inc_s2", ws=incident.ws,
                   candidates=orchestrator.find_candidates(incident.eng.record))


# ---------- tools ----------

def test_confirm_building_tool_only_accepts_a_candidate(session):
    confirm = make_tools(session)["confirm_building"]
    assert confirm.invoke({"candidates": [BUILDING]}).startswith("Error")
    assert confirm.invoke({"candidates": ["x"], "chosen_building_id": "x"}).startswith("Error")
    assert orchestrator._incident(session.ws).confirmed_building_id is None
    assert BUILDING in confirm.invoke({"candidates": [BUILDING], "chosen_building_id": BUILDING})
    saved = orchestrator._incident(session.ws)
    assert (saved.confirmed_building_id, saved.status) == (BUILDING, "confirmed")


def test_load_dossier_needs_confirmation_and_is_not_repeated(session, incident):
    load = make_tools(session)["load_dossier"]
    assert "not confirmed" in load.invoke({})
    incident.confirm()
    assert "ev_costar_1" in load.invoke({})
    assert BuildingDossier.model_validate_json((session.ws / "dossier.json").read_text()).building_id == BUILDING
    written = (session.ws / "dossier.json").stat().st_mtime_ns
    assert load.invoke({}).startswith("Already loaded")
    assert (session.ws / "dossier.json").stat().st_mtime_ns == written


def test_submit_considerations_writes_the_current_version(session, incident):
    tools = make_tools(session)
    incident.confirm()
    assert "does not exist yet" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    tools["load_dossier"].invoke({})
    assert "brief_v1.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    v1 = Brief.model_validate_json((session.ws / "brief_v1.json").read_text())
    assert [c.title for c in v1.considerations] == [GOOD["title"]] and v1.changes_since_last == []

    incident.replay("c2")
    session.version, session.accepted, session.rejections = 2, 0, 0
    assert "brief_v2.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    v2 = Brief.model_validate_json((session.ws / "brief_v2.json").read_text())
    assert v2.version == 2 and "Floor contradicted: 3 vs 4 (Call 2)" in v2.changes_since_last


def test_rejections_are_counted_into_the_brief(session, incident):
    tools = make_tools(session)
    incident.confirm()
    tools["load_dossier"].invoke({})
    bad = [{**GOOD, "title": "Send the truck to Stair B"}, {**GOOD, "evidence_ids": ["ev_made_up"]}, GOOD]
    out = tools["submit_considerations"].invoke({"considerations": bad})
    assert out.startswith("Rejected") and "#1" in out and "ev_made_up" in out
    assert not (session.ws / "brief_v1.json").exists()

    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert "needs at least one issue" in tools["submit_critique"].invoke({"verdict": "revise", "issues": []})
    tools["submit_critique"].invoke({"verdict": "revise", "issues": ["c1: not supported"]})
    assert json.loads((session.ws / "critique_v1.json").read_text())["verdict"] == "revise"
    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert not (session.ws / "critique_v1.json").exists()  # it was about an older draft
    brief = Brief.model_validate_json((session.ws / "brief_v1.json").read_text())
    assert brief.verifier_rejections == 3  # two by the code checks, one by the critic


def test_regeneration_budget(session, incident):
    tools = make_tools(session)
    incident.confirm()
    tools["load_dossier"].invoke({})
    for _ in range(MAX_SUBMISSIONS):
        assert tools["submit_considerations"].invoke({"considerations": [GOOD]}).startswith("Accepted")
    assert "budget used up" in tools["submit_considerations"].invoke({"considerations": [GOOD]})


# ---------- start / resume / update with a scripted model (no API) ----------

class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted(*steps) -> ScriptedModel:
    """Each step is a final text or a list of (tool name, args). One shared script, in call order."""
    def messages():
        for i, step in enumerate(steps):
            if isinstance(step, str):
                yield AIMessage(content=step)
            else:
                yield AIMessage(content="", tool_calls=[
                    {"name": name, "args": args, "id": f"call_{i}_{j}", "type": "tool_call"}
                    for j, (name, args) in enumerate(step)])
        for _ in range(5):  # a wrong turn should fail an assertion, not raise StopIteration
            yield AIMessage(content="(script exhausted)")
    return ScriptedModel(messages=messages())


def todos(*statuses: str) -> tuple[str, dict]:
    names = ["Confirm building with dispatcher", "Load building dossier", "Draft considerations",
             "Critique brief", "Brief ready", "Redraft considerations (v2)", "Critique brief v2"]
    return "write_todos", {"todos": [{"content": n, "status": s} for n, s in zip(names, statuses)]}


def task(subagent: str) -> tuple[str, dict]:
    return "task", {"subagent_type": subagent, "description": f"Incident inc_s2: do the {subagent} step."}


FULL_SCRIPT = [
    # start_incident
    [todos("in_progress", "pending", "pending", "pending", "pending")],
    [("confirm_building", {"candidates": [BUILDING]})],          # pauses here
    # resume_incident
    [task("dossier")], [("load_dossier", {})], "Dossier loaded.",
    [task("strategy")], [("submit_considerations", {"considerations": [GOOD]})], "Wrote /workspace/brief_v1.json.",
    [task("critic")], [("submit_critique", {"verdict": "pass", "issues": []})], "pass",
    [todos(*["completed"] * 5)], "pass",
    # update_incident
    [todos(*["completed"] * 5, "in_progress", "pending"), task("strategy")],
    [("submit_considerations", {"considerations": [{**GOOD, "title": "Consider Stair B (back stairs)"}]})],
    "Wrote /workspace/brief_v2.json.",
    [task("critic")], [("submit_critique", {"verdict": "pass", "issues": []})], "pass",
    [todos(*["completed"] * 7)], "pass",
]


def test_start_pauses_resume_briefs_update_reuses_the_thread_and_dossier(incident):
    seen: list[Progress] = []
    started = start_incident("inc_s2", workspace_root=incident.root, on_todos=seen.append,
                             model=scripted(*FULL_SCRIPT))
    assert started.status == "awaiting_confirmation" and started.brief is None
    assert [c.building_id for c in started.candidates] == [BUILDING]
    paused = Progress.model_validate_json((incident.ws / "todos.json").read_text())
    assert paused.status == "awaiting_confirmation" and paused.todos[0].status == "in_progress"
    assert not (incident.ws / "dossier.json").exists()  # nothing happens before the dispatcher answers
    assert orchestrator._incident(incident.ws).confirmed_building_id is None

    with pytest.raises(ValueError, match="not one of the candidates"):
        resume_incident("inc_s2", "some_other_building")
    with pytest.raises(ValueError, match="no brief to update"):
        update_incident("inc_s2", ["x"])

    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.version, v1.fallback_reason) == ("briefed", 1, None)
    assert orchestrator._incident(incident.ws).confirmed_building_id == BUILDING
    assert [c.title for c in v1.brief.considerations] == [GOOD["title"]]
    assert v1.critique["verdict"] == "pass" and all(t.status == "completed" for t in v1.todos)
    dossier_written = (incident.ws / "dossier.json").stat().st_mtime_ns
    session = orchestrator._SESSIONS["inc_s2"]
    messages_after_v1 = len(session.agent.get_state(session.config).values["messages"])

    with pytest.raises(ValueError, match="not awaiting confirmation"):
        resume_incident("inc_s2", BUILDING)

    v2 = update_incident("inc_s2", incident.replay("c2"))
    assert (v2.status, v2.version, v2.fallback_reason) == ("briefed", 2, None)
    on_disk = Brief.model_validate_json((incident.ws / "brief_v2.json").read_text())
    assert on_disk.changes_since_last[0] == "Floor contradicted: 3 vs 4 (Call 2)"
    assert "Considerations redrafted: 1 (was 1)" in on_disk.changes_since_last
    assert (incident.ws / "brief_v1.json").exists() and (incident.ws / "critique_v2.json").exists()
    assert (incident.ws / "dossier.json").stat().st_mtime_ns == dossier_written  # dossier reused
    assert len(session.agent.get_state(session.config).values["messages"]) > messages_after_v1  # same thread
    assert len(v2.todos) == 7 and seen[-1].status == "done" and seen[-1].version == 2


def test_dispatcher_can_reject_every_candidate(incident):
    script = FULL_SCRIPT[:2] + ["No building confirmed."]
    start_incident("inc_s2", workspace_root=incident.root, model=scripted(*script))
    result = resume_incident("inc_s2", None)
    assert result.status == "rejected" and result.brief is None
    assert orchestrator._incident(incident.ws).confirmed_building_id is None
    assert not list(incident.ws.glob("brief_v*.json"))
    assert Progress.model_validate_json((incident.ws / "todos.json").read_text()).status == "rejected"


def test_start_needs_a_location(tmp_path):
    ws = tmp_path / "inc_x"
    ws.mkdir()
    (ws / "incident.json").write_text(FusionEngine("inc_x").record.model_dump_json())
    with pytest.raises(ValueError, match="no candidate buildings"):
        start_incident("inc_x", workspace_root=tmp_path)
    with pytest.raises(ValueError, match="call start_incident first"):
        resume_incident("inc_never_started", BUILDING)


def test_broken_agent_still_gets_a_facts_only_brief(incident, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(orchestrator, "build_orchestrator", boom)
    started = start_incident("inc_s2", workspace_root=incident.root)
    assert started.status == "awaiting_confirmation" and started.candidates  # dispatcher can still choose
    result = resume_incident("inc_s2", BUILDING)
    brief = Brief.model_validate_json((incident.ws / "brief_v1.json").read_text())
    assert result.status == "briefed" and result.fallback_reason and brief.considerations == []
    assert brief.gaps[0].startswith("Considerations withheld") and brief.location_confirmed
    assert brief.size_up["building.stories"].value == 6

    v2 = update_incident("inc_s2", incident.replay("c2"))  # updates degrade the same way
    assert v2.fallback_reason and "Floor contradicted: 3 vs 4 (Call 2)" in v2.brief.changes_since_last


# ---------- live ----------

@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_FAST_MODEL")),
                    reason="GEMINI_API_KEY / GEMINI_FAST_MODEL not set")
def test_orchestrator_end_to_end_on_s2(incident):
    started = start_incident("inc_s2", workspace_root=incident.root)
    assert started.status == "awaiting_confirmation"
    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    v2 = update_incident("inc_s2", incident.replay("c2"))
    orchestrator.flush_traces()
    for result in (v1, v2):
        brief = Brief.model_validate_json((incident.ws / f"brief_v{result.version}.json").read_text())
        assert len(brief.considerations) <= 5
        assert all(c.title.startswith("Consider ") and c.evidence_ids for c in brief.considerations)
        assert all(e in brief.evidence for c in brief.considerations for e in c.evidence_ids)
        assert result.fallback_reason or brief.considerations
        assert all(t.status == "completed" for t in result.todos)
    assert "Floor contradicted: 3 vs 4 (Call 2)" in v2.brief.changes_since_last
    assert (incident.ws / "notes" / "dossier_summary.md").exists()
    assert (incident.ws / "notes" / "strategy_reasoning.md").exists()
