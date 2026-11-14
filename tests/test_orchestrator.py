import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from flamescout_schemas import Brief, BuildingDossier, CallSegment, Progress
from services.agents import orchestrator
from services.agents.dossier import LAYERS
from services.agents.fusion import FusionEngine
from services.agents.orchestrator import (MAX_SUBMISSIONS, Session, make_tools, resume_incident,
                                          start_incident, update_incident)

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
BUILDING = "fake_wilshire_tower"
GOOD = {"category": "access", "title": "Consider Stair B from the rear alley",
        "rationale": "Stair B discharges to the rear alley, which has vehicle access.",
        "evidence_ids": ["ev_scan_fp_1", "ev_aerial_2"]}  # matterport + vexcel
SINGLE_LAYER = {**GOOD, "title": "Consider the standpipe in Stair A", "evidence_ids": ["ev_scan_fp_4"]}


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


def test_pull_tools_write_layers_then_merge_and_do_not_repeat(session, incident):
    tools = make_tools(session)
    assert "not confirmed" in tools["pull_costar"].invoke({})
    incident.confirm()
    assert "ev_costar_1" in tools["pull_costar"].invoke({})
    assert "ev_scan_fp_1" in tools["pull_matterport"].invoke({})
    assert (session.ws / "layers" / "costar.json").exists() and not (session.ws / "dossier.json").exists()
    assert "ev_aerial_2" in tools["pull_vexcel"].invoke({})  # third layer: code merges
    dossier = BuildingDossier.model_validate_json((session.ws / "dossier.json").read_text())
    assert dossier.building_id == BUILDING and {e.source for e in dossier.evidence.values()} == set(LAYERS)

    written = (session.ws / "layers" / "vexcel.json").stat().st_mtime_ns
    assert tools["pull_vexcel"].invoke({}).startswith("Already pulled")
    assert (session.ws / "layers" / "vexcel.json").stat().st_mtime_ns == written


def test_submit_fills_in_layers_a_subagent_did_not_pull(session, incident):
    tools = make_tools(session)
    assert "not confirmed" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    incident.confirm()
    tools["pull_costar"].invoke({})  # matterport and vexcel never ran
    assert "brief_v1.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert all((session.ws / "layers" / f"{name}.json").exists() for name in LAYERS)
    v1 = Brief.model_validate_json((session.ws / "brief_v1.json").read_text())
    assert [(c.title, c.cross_layer) for c in v1.considerations] == [(GOOD["title"], True)]
    assert v1.changes_since_last == []

    incident.replay("c2")
    session.version, session.accepted, session.rejections = 2, 0, 0
    assert "brief_v2.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    v2 = Brief.model_validate_json((session.ws / "brief_v2.json").read_text())
    assert v2.version == 2 and "Floor contradicted: 3 vs 4 (Call 2)" in v2.changes_since_last


def test_rejections_are_counted_into_the_brief(session, incident):
    tools = make_tools(session)
    incident.confirm()
    bad = [{**GOOD, "title": "Send the truck to Stair B"}, {**GOOD, "evidence_ids": ["ev_made_up"]}, GOOD]
    out = tools["submit_considerations"].invoke({"considerations": bad})
    assert out.startswith("Rejected") and "#1" in out and "ev_made_up" in out
    out = tools["submit_considerations"].invoke({"considerations": [SINGLE_LAYER]})
    assert out.startswith("Rejected") and "combines data layers" in out
    assert not (session.ws / "brief_v1.json").exists()

    tools["submit_considerations"].invoke({"considerations": [GOOD, SINGLE_LAYER]})
    assert "needs at least one issue" in tools["submit_critique"].invoke({"verdict": "revise", "issues": []})
    tools["submit_critique"].invoke({"verdict": "revise", "issues": ["c1: not supported"]})
    assert json.loads((session.ws / "critique_v1.json").read_text())["verdict"] == "revise"
    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert not (session.ws / "critique_v1.json").exists()  # it was about an older draft
    brief = Brief.model_validate_json((session.ws / "brief_v1.json").read_text())
    assert brief.verifier_rejections == 4  # 2 faulty drafts + 1 missing cross-layer + 1 critic issue


def test_regeneration_budget(session, incident):
    tools = make_tools(session)
    incident.confirm()
    for _ in range(MAX_SUBMISSIONS):
        assert tools["submit_considerations"].invoke({"considerations": [GOOD]}).startswith("Accepted")
    assert "budget used up" in tools["submit_considerations"].invoke({"considerations": [GOOD]})


# ---------- start / resume / update with a fake model (no API) ----------

def calls(*tool_calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"call_{uuid.uuid4().hex[:8]}",
                                              "type": "tool_call"} for n, a in tool_calls])


class RuleModel(BaseChatModel):
    """Answers by which agent is asking (known from the tools it was given), not by call order:
    the three layer subagents run concurrently, so their model calls interleave."""
    main_script: Any            # iterator of the orchestrator's turns, shared by every bound copy
    drafts: Any                 # one considerations list per strategy run
    tool_names: tuple = ()

    @property
    def _llm_type(self) -> str:
        return "rule-model"

    def bind_tools(self, tools, **kwargs):
        names = tuple(getattr(t, "name", None) or t.get("name") or t["function"]["name"] for t in tools)
        return self.model_copy(update={"tool_names": names})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _reply(self, messages) -> AIMessage:
        if "task" in self.tool_names:  # the orchestrator: its turns are sequential, so a script works
            return next(self.main_script, AIMessage(content="(script exhausted)"))
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Done.")
        for layer in LAYERS:
            if f"pull_{layer}" in self.tool_names:
                return calls((f"pull_{layer}", {}))
        if "submit_considerations" in self.tool_names:
            return calls(("submit_considerations", {"considerations": self.drafts.pop(0)}))
        return calls(("submit_critique", {"verdict": "pass", "issues": []}))


TODOS = ["Confirm building with dispatcher", "CoStar property record", "Matterport interior scan",
         "Vexcel aerial imagery", "Draft considerations", "Critique brief", "Brief ready",
         "Redraft considerations (v2)", "Critique brief v2"]


def todos(*statuses: str) -> tuple[str, dict]:
    return "write_todos", {"todos": [{"content": n, "status": s} for n, s in zip(TODOS, statuses)]}


def task(subagent: str) -> tuple[str, dict]:
    return "task", {"subagent_type": subagent, "description": f"Incident inc_s2: do the {subagent} step."}


def fake_model(*main_turns) -> RuleModel:
    drafts = [[GOOD, SINGLE_LAYER], [{**GOOD, "title": "Consider Stair B (back stairs) from the alley"}]]
    return RuleModel(main_script=iter(main_turns), drafts=drafts)


START = [calls(todos("in_progress", *["pending"] * 6)),
         calls(("confirm_building", {"candidates": [BUILDING]}))]          # pauses here
RESUME = [calls(todos("completed", "in_progress", "in_progress", "in_progress", "pending", "pending", "pending"),
                task("costar"), task("matterport"), task("vexcel")),       # one turn: three in parallel
          calls(task("strategy")), calls(task("critic")),
          calls(todos(*["completed"] * 7)), AIMessage(content="pass")]
UPDATE = [calls(todos(*["completed"] * 7, "in_progress", "pending"), task("strategy")),
          calls(task("critic")), calls(todos(*["completed"] * 9)), AIMessage(content="pass")]


def test_start_pauses_resume_briefs_update_reuses_thread_and_layers(incident, monkeypatch):
    # All three layer pulls must be in flight at once, or the barrier breaks and the pulls fail.
    barrier = threading.Barrier(3, timeout=10)
    real_pull = orchestrator.dossier_mod.pull_layer

    def pull_together(layer, building_id, providers=None):
        barrier.wait()
        return real_pull(layer, building_id, providers)

    monkeypatch.setattr(orchestrator.dossier_mod, "pull_layer", pull_together)

    seen: list[Progress] = []
    started = start_incident("inc_s2", workspace_root=incident.root, on_todos=seen.append,
                             model=fake_model(*START, *RESUME, *UPDATE))
    assert started.status == "awaiting_confirmation" and started.brief is None
    assert [c.building_id for c in started.candidates] == [BUILDING]
    paused = Progress.model_validate_json((incident.ws / "todos.json").read_text())
    assert paused.status == "awaiting_confirmation" and paused.todos[0].status == "in_progress"
    assert not (incident.ws / "layers").exists()  # nothing happens before the dispatcher answers
    assert orchestrator._incident(incident.ws).confirmed_building_id is None

    with pytest.raises(ValueError, match="not one of the candidates"):
        resume_incident("inc_s2", "some_other_building")
    with pytest.raises(ValueError, match="no brief to update"):
        update_incident("inc_s2", ["x"])

    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.version, v1.fallback_reason) == ("briefed", 1, None)
    assert not barrier.broken and all((incident.ws / "layers" / f"{name}.json").exists() for name in LAYERS)
    assert orchestrator._incident(incident.ws).confirmed_building_id == BUILDING
    assert [c.cross_layer for c in v1.brief.considerations] == [True, False]
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
    assert "Considerations redrafted: 1 (was 2)" in on_disk.changes_since_last
    assert (incident.ws / "brief_v1.json").exists() and (incident.ws / "critique_v2.json").exists()
    assert (incident.ws / "dossier.json").stat().st_mtime_ns == dossier_written  # data layers reused
    assert len(session.agent.get_state(session.config).values["messages"]) > messages_after_v1  # same thread
    assert len(v2.todos) == 9 and seen[-1].status == "done" and seen[-1].version == 2


def test_dispatcher_can_reject_every_candidate(incident):
    start_incident("inc_s2", workspace_root=incident.root,
                   model=fake_model(*START, AIMessage(content="No building confirmed.")))
    result = resume_incident("inc_s2", None)
    assert result.status == "rejected" and result.brief is None
    assert orchestrator._incident(incident.ws).confirmed_building_id is None
    assert not list(incident.ws.glob("brief_v*.json")) and not (incident.ws / "layers").exists()
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
    assert brief.size_up["building.stories"].value == 6 and len(brief.markers) == 10

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
        assert result.fallback_reason or any(c.cross_layer for c in brief.considerations)
        assert all(t.status == "completed" for t in result.todos)
    assert "Floor contradicted: 3 vs 4 (Call 2)" in v2.brief.changes_since_last
    assert all((incident.ws / "notes" / f"{name}_summary.md").exists() for name in LAYERS)
    assert (incident.ws / "notes" / "strategy_reasoning.md").exists()
