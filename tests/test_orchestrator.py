import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from datetime import timedelta

from flamescout_schemas import Brief, BuildingDossier, CallSegment, Progress, now
from services.agents import cache, copilot, memory, orchestrator
from services.agents.dossier import LAYERS
from services.agents.fusion import FusionEngine
from services.agents.orchestrator import (MAX_SUBMISSIONS, NOTE_HEADER, Session, make_tools,
                                          prefetch_building, resume_incident, review_brief, save_incident,
                                          start_incident, update_incident)
from services.agents.overlay import validate_overlay
from services.agents.workspace import incident_workspace

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
BUILDING = "fake_wilshire_tower"
GOOD = {"category": "access", "title": "Consider Stair B from the rear alley",
        "rationale": "Stair B discharges to the rear alley, which has vehicle access.",
        "evidence_ids": ["ev_scan_fp_1", "ev_aerial_2"]}  # matterport + vexcel
SINGLE_LAYER = {**GOOD, "title": "Consider the standpipe in Stair A", "evidence_ids": ["ev_scan_fp_4"]}
DEPOT = "fake_quarry_depot"  # no interior scan
ROLLER_DOOR = {"category": "access", "title": "Consider the roller door from the loading yard",
               "rationale": "A roller door seen from the air opens onto the yard beside the pool-supply unit.",
               "evidence_ids": ["ev_qd_aerial_2", "ev_qd_costar_2"]}  # vexcel + costar, aerial entry conf 0.4
POOL_CHEMICALS = {"category": "hazard", "title": "Consider possible pool chemicals in unit A",
                  "rationale": "A pool and spa chemical wholesaler occupies unit A.", "evidence_ids": ["ev_qd_costar_2"]}


def note(layer: str, *replies: str) -> str:
    """What code saves for a layer subagent's replies: the unverified-notes header, then each reply."""
    return NOTE_HEADER.format(layer=layer) + "\n\n".join(replies) + "\n"


class Incident:
    """A script replayed call by call into a fresh workspace (tmp files or mongomock), like scripts/run_incident.py."""

    def __init__(self, root, script: str = "s2"):
        self.root = root  # None = MongoDB
        self.id = f"inc_{script}"
        self.ws = incident_workspace(self.id, root)
        self.segments = [CallSegment(**s) for s in json.loads((CALLS / f"{script}.json").read_text())["segments"]]
        self.eng = FusionEngine(self.id)

    def replay(self, call_id: str) -> list[str]:
        segments = [s for s in self.segments if s.call_id == call_id]
        notes = [n for s in segments for n in self.eng.ingest(s)]
        save_incident(self.eng.record, segments, self.root)
        return notes

    def confirm(self) -> None:
        self.eng.confirm(BUILDING)
        save_incident(self.eng.record, workspace_root=self.root)


@pytest.fixture
def incident(storage):
    inc = Incident(storage)
    inc.replay("c1")
    return inc


@pytest.fixture
def depot(storage):
    """S4B: one call to the building with no interior scan."""
    inc = Incident(storage, "s4b")
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


def test_confirm_reuses_a_fresh_prefetch_cache(session, make_cache):
    at = make_cache(BUILDING)
    reply = make_tools(session)["confirm_building"].invoke({"candidates": [BUILDING], "chosen_building_id": BUILDING})
    assert "PREFETCHED" in reply and session.prefetched_at == at
    assert all(session.ws.exists(f"layers/{name}.json") for name in LAYERS)
    assert session.ws.exists("dossier.json")
    assert session.ws.read("notes/costar_summary.md") == "cached costar note\n"
    assert make_tools(session)["pull_vexcel"].invoke({}).startswith("Already pulled")


@pytest.mark.parametrize("use_cache, age_hours", [(False, 0), (True, 25)])
def test_confirm_ignores_the_cache_when_cold_or_stale(session, make_cache, use_cache, age_hours):
    make_cache(BUILDING, at=now() - timedelta(hours=age_hours))
    session.use_cache = use_cache
    reply = make_tools(session)["confirm_building"].invoke({"candidates": [BUILDING], "chosen_building_id": BUILDING})
    assert "No prefetched data" in reply and session.prefetched_at is None
    assert not session.ws.exists("layers")


def test_pull_tools_write_layers_then_merge_and_do_not_repeat(session, incident):
    tools = make_tools(session)
    assert "not confirmed" in tools["pull_costar"].invoke({})
    incident.confirm()
    assert "ev_costar_1" in tools["pull_costar"].invoke({})
    assert "ev_scan_fp_1" in tools["pull_matterport"].invoke({})
    assert session.ws.exists("layers/costar.json") and not session.ws.exists("dossier.json")
    assert "ev_aerial_2" in tools["pull_vexcel"].invoke({})  # third layer: code merges
    dossier = BuildingDossier.model_validate_json(session.ws.read("dossier.json"))
    assert dossier.building_id == BUILDING and {e.source for e in dossier.evidence.values()} == set(LAYERS)

    written = session.ws.modified("layers/vexcel.json")
    assert tools["pull_vexcel"].invoke({}).startswith("Already pulled")
    assert session.ws.modified("layers/vexcel.json") == written


def test_submit_fills_in_layers_a_subagent_did_not_pull(session, incident):
    tools = make_tools(session)
    assert "not confirmed" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    incident.confirm()
    tools["pull_costar"].invoke({})  # matterport and vexcel never ran
    assert "brief_v1.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert all(session.ws.exists(f"layers/{name}.json") for name in LAYERS)
    v1 = Brief.model_validate_json(session.ws.read("brief_v1.json"))
    assert [(c.title, c.cross_layer) for c in v1.considerations] == [(GOOD["title"], True)]
    assert v1.changes_since_last == []

    incident.replay("c2")
    session.version, session.accepted, session.rejections = 2, 0, 0
    assert "brief_v2.json" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    v2 = Brief.model_validate_json(session.ws.read("brief_v2.json"))
    assert v2.version == 2 and "Floor contradicted: 3 vs 4 (Call 2)" in v2.changes_since_last


def test_rejections_are_counted_into_the_brief(session, incident):
    tools = make_tools(session)
    incident.confirm()
    bad = [{**GOOD, "title": "Send the truck to Stair B"}, {**GOOD, "evidence_ids": ["ev_made_up"]}, GOOD]
    out = tools["submit_considerations"].invoke({"considerations": bad})
    assert out.startswith("Rejected") and "#1" in out and "ev_made_up" in out
    out = tools["submit_considerations"].invoke({"considerations": [SINGLE_LAYER]})
    assert out.startswith("Rejected") and "combines data layers" in out
    assert not session.ws.exists("brief_v1.json")

    tools["submit_considerations"].invoke({"considerations": [GOOD, SINGLE_LAYER]})
    assert "needs at least one issue" in tools["submit_critique"].invoke({"verdict": "revise", "issues": []})
    tools["submit_critique"].invoke({"verdict": "revise", "issues": ["c1: not supported"]})
    assert json.loads(session.ws.read("critique_v1.json"))["verdict"] == "revise"
    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert not session.ws.exists("critique_v1.json")  # it was about an older draft
    brief = Brief.model_validate_json(session.ws.read("brief_v1.json"))
    assert brief.verifier_rejections == 4  # 2 faulty drafts + 1 missing cross-layer + 1 critic issue


def test_regeneration_budget(session, incident):
    tools = make_tools(session)
    incident.confirm()
    for _ in range(MAX_SUBMISSIONS):
        assert tools["submit_considerations"].invoke({"considerations": [GOOD]}).startswith("Accepted")
    assert "budget used up" in tools["submit_considerations"].invoke({"considerations": [GOOD]})


def test_copilot_question_tool_needs_a_life_safety_contradiction(session, incident):
    ask = make_tools(session)["write_copilot_question"]
    assert ask.invoke({"key": "incident.floor"}).startswith("Error")  # call 1 alone: no contradiction
    assert not session.ws.exists("copilot_questions.json")
    incident.replay("c2")
    reply = ask.invoke({"key": "incident.floor"})
    assert "Callers disagree on floor (3 vs 4)" in reply
    [q] = copilot.read_questions(session.ws)
    assert (q.field, q.status, q.reason) == ("incident.floor", "open", "contradiction")
    assert q.question == "Callers disagree on floor (3 vs 4). Ask: which floor are the flames or smoke on?"
    assert ask.invoke({"key": "people.trapped"}).startswith("Error")


def test_publish_tool_waits_for_the_critic_and_applies_drops(session, incident):
    tools = make_tools(session)
    incident.confirm()
    assert "does not exist yet" in tools["publish_brief"].invoke({})
    tools["submit_considerations"].invoke({"considerations": [GOOD, SINGLE_LAYER]})
    assert "critic has not passed" in tools["publish_brief"].invoke({})
    tools["submit_critique"].invoke({"verdict": "pass", "issues": []})
    assert orchestrator._needs_review(session) == []  # nothing below 0.5 here
    assert "Not in inc_s2_v1" in tools["publish_brief"].invoke({"drop": ["c9"]})
    assert not orchestrator._brief(session).published
    assert "Dropped by the dispatcher: c2" in tools["publish_brief"].invoke({"drop": ["c2"]})
    brief = orchestrator._brief(session)
    assert brief.published and [(c.id, c.rank) for c in brief.considerations] == [("c1", 1)]
    assert "already published" in tools["publish_brief"].invoke({})


def test_critic_note_flags_are_recorded_and_written_into_the_note(session, incident):
    tools = make_tools(session)
    incident.confirm()
    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    session.ws.write("notes/matterport_summary.md", note("matterport", "Standpipes on every floor [ev_scan_fp_4]"))
    out = tools["submit_critique"].invoke({"verdict": "pass", "issues": [], "note_flags": [
        {"layer": "matterport", "claim": "Standpipes on every floor"}]})
    assert "1 note claim(s) flagged" in out
    assert json.loads(session.ws.read("critique_v1.json"))["note_flags"] == [
        {"layer": "matterport", "claim": "Standpipes on every floor"}]
    text = session.ws.read("notes/matterport_summary.md")
    assert text.startswith("> Unverified agent notes")
    assert text.endswith("> Critic flag, not supported by layers/matterport.json: Standpipes on every floor\n")


def test_exterior_entries_are_pulled_only_on_request_or_as_a_safety_net(depot):
    s = Session(incident_id="inc_s4b", ws=depot.ws, candidates=orchestrator.find_candidates(depot.eng.record))
    tools = make_tools(s)
    tools["confirm_building"].invoke({"candidates": [DEPOT], "chosen_building_id": DEPOT})
    for name in LAYERS:
        tools[f"pull_{name}"].invoke({})
    assert "not available" in tools["pull_matterport"].invoke({})
    assert [m.id for m in orchestrator._dossier(s.ws).markers] == ["m_qd_yard"]  # no entries yet
    assert "m_qd_ext_roller" in tools["pull_vexcel_entries"].invoke({})
    assert {m.id for m in orchestrator._dossier(s.ws).markers} == {"m_qd_yard", "m_qd_ext_roller", "m_qd_ext_front"}
    assert tools["pull_vexcel_entries"].invoke({}).startswith("Already pulled")

    # Safety net: an agent that never re-planned still gets the entries before a brief is built.
    s.ws.delete("layers/vexcel.json"), s.ws.delete("dossier.json")
    tools["pull_vexcel"].invoke({})
    assert [m.id for m in orchestrator._dossier(s.ws).markers] == ["m_qd_yard"]
    assert tools["submit_considerations"].invoke({"considerations": [ROLLER_DOOR]}).startswith("Accepted")
    brief = orchestrator._brief(s)
    assert "No interior scan on file" in brief.gaps and brief.considerations[0].confidence == 0.4


# ---------- start / resume / update with a fake model (no API) ----------

def calls(*tool_calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"call_{uuid.uuid4().hex[:8]}",
                                              "type": "tool_call"} for n, a in tool_calls])


class RuleModel(BaseChatModel):
    """Answers by which agent is asking (known from the tools it was given), not by call order:
    the three layer subagents run concurrently, so their model calls interleave."""
    main_script: Any            # iterator of the orchestrator's turns, shared by every bound copy
    drafts: Any                 # one considerations list per strategy run
    seen: Any                   # agent name -> its system prompt, shared by every bound copy
    stall: Any                  # agent name -> how many of its next calls time out, shared
    overlays: Any = None        # the incident mapper's submissions, in order (each a draft dict)
    tool_names: tuple = ()

    @property
    def _llm_type(self) -> str:
        return "rule-model"

    def bind_tools(self, tools, **kwargs):
        names = tuple(getattr(t, "name", None) or t.get("name") or t["function"]["name"] for t in tools)
        return self.model_copy(update={"tool_names": names})

    def _who(self) -> str:
        if "task" in self.tool_names:
            return "orchestrator"
        for name, tool_name in [*((layer, f"pull_{layer}") for layer in LAYERS),
                                ("strategy", "submit_considerations"), ("critic", "submit_critique"),
                                ("incident_mapper", "submit_overlay")]:
            if tool_name in self.tool_names:
                return name
        return "other"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.stall.get(self._who(), 0) > 0:  # like the API: no answer before the client timeout
            self.stall[self._who()] -= 1
            raise TimeoutError("simulated model stall")
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _reply(self, messages) -> AIMessage:
        if "task" in self.tool_names:  # the orchestrator: its turns are sequential, so a script works
            return next(self.main_script, AIMessage(content="(script exhausted)"))
        called = any(isinstance(m, ToolMessage) for m in messages)
        asked = " ".join(m.text for m in messages if m.type == "human")
        for layer in LAYERS:
            if f"pull_{layer}" in self.tool_names:  # two calls: pull, then the summary code saves
                if layer == "vexcel" and "EXTERIOR ENTRIES" in asked:  # the no-scan re-plan
                    return (AIMessage(content="vexcel exterior entries [ev_x]") if called
                            else calls(("pull_vexcel_entries", {})))
                return AIMessage(content=f"{layer} summary line [ev_x]") if called else calls((f"pull_{layer}", {}))
        if "submit_considerations" in self.tool_names:
            return self._strategy(messages, asked, called)
        if "submit_overlay" in self.tool_names:
            return self._mapper(messages, asked, called)
        if called:
            return AIMessage(content="Done.")
        return calls(("submit_critique", {"verdict": "pass", "issues": []}))

    def _mapper(self, messages, asked: str, called: bool) -> AIMessage:
        """Reads the transcript and the plan index in one turn, submits, and resubmits when rejected."""
        if not called:
            self.seen.setdefault("mapper_tasks", []).append(asked)
            return calls(("get_transcript", {}), ("get_plan_index", {}))
        results = [m for m in messages if isinstance(m, ToolMessage)]
        submitted = [m for m in results if m.name == "submit_overlay"]
        if submitted and not submitted[-1].text.startswith("Rejected"):
            return AIMessage(content="Done.")
        if not submitted:
            self.seen["plan_index"] = next(m.text for m in results if m.name == "get_plan_index")
        else:
            self.seen.setdefault("overlay_rejections", []).append(submitted[-1].text)
        if not self.overlays:
            return AIMessage(content="Nothing more to submit.")
        return calls(("submit_overlay", {"overlay": self.overlays.pop(0)}))

    def _strategy(self, messages, asked: str, called: bool) -> AIMessage:
        """Reads the crew memory file its task names, submits a draft, and resubmits when rejected."""
        last = messages[-1].text if called else ""
        if last.startswith("Accepted") or last.startswith("Error"):
            return AIMessage(content="Done.")
        path = re.search(r"/memories/\S+\.md", asked)
        if path and not called:
            return calls(("read_file", {"file_path": path.group(0), "limit": 1000}))
        if called and not last.startswith("Rejected"):
            self.seen["memory_read"] = last  # what read_file returned through the /memories/ mount
        self.seen["strategy"] = " ".join(m.text for m in messages if m.type == "system")
        return calls(("submit_considerations", {"considerations": self.drafts.pop(0)}))


TODOS = ["Confirm building with dispatcher", "CoStar property record", "Matterport interior scan",
         "Vexcel aerial imagery", "Draft considerations", "Critique brief", "Brief ready",
         "Redraft considerations (v2)", "Critique brief v2", "Ask dispatcher: floor contradiction"]
# The plan after the no-scan re-plan: interior step renamed, exterior entries inserted.
DEPOT_TODOS = ["Confirm building with dispatcher", "CoStar property record",
               "No interior scan on file: interior steps skipped", "Vexcel aerial imagery",
               "Exterior entries from aerial imagery", "Draft considerations", "Critique brief", "Brief ready"]


def todos(*statuses: str, names: list[str] = TODOS) -> tuple[str, dict]:
    return "write_todos", {"todos": [{"content": n, "status": s} for n, s in zip(names, statuses)]}


def task(subagent: str, ask: str = "") -> tuple[str, dict]:
    return "task", {"subagent_type": subagent, "description": f"Incident: do the {subagent} step. {ask}"}


def fake_model(*main_turns, drafts=None, stall=None, overlays=None) -> RuleModel:
    drafts = drafts or [[GOOD, SINGLE_LAYER], [{**GOOD, "title": "Consider Stair B (back stairs) from the alley"}]]
    return RuleModel(main_script=iter(main_turns), drafts=drafts, seen={}, stall=stall or {}, overlays=overlays or [])


START = [calls(todos("in_progress", *["pending"] * 6)),
         calls(("confirm_building", {"candidates": [BUILDING]}))]          # pauses here
RESUME = [calls(todos("completed", "in_progress", "in_progress", "in_progress", "pending", "pending", "pending"),
                task("costar"), task("matterport"), task("vexcel")),       # one turn: three in parallel
          calls(task("strategy")), calls(task("critic")),
          calls(todos(*["completed"] * 7), ("publish_brief", {})), AIMessage(content="pass")]
UPDATE = [calls(todos(*["completed"] * 7, "in_progress", "pending", "completed"),
                ("write_copilot_question", {"key": "incident.floor"}), task("strategy")),  # floor 3 vs 4
          calls(task("critic")), calls(todos(*["completed"] * 10), ("publish_brief", {})), AIMessage(content="pass")]
PREFETCH = [calls(task("costar"), task("matterport"), task("vexcel")), AIMessage(content="done")]
WARM_RESUME = [calls(todos(*["completed"] * 4, "in_progress", "pending", "pending"), task("strategy")),
               calls(task("critic")), calls(todos(*["completed"] * 7), ("publish_brief", {})),
               AIMessage(content="pass")]
# S4, no interior scan: layers, then the re-plan turn, then draft / critique / publish (which pauses).
DEPOT_START = [calls(todos("in_progress", *["pending"] * 6)),
               calls(("confirm_building", {"candidates": [DEPOT]}))]
DEPOT_RESUME = [
    calls(todos("completed", "in_progress", "in_progress", "in_progress", "pending", "pending", "pending"),
          task("costar"), task("matterport"), task("vexcel")),
    calls(todos("completed", "completed", "completed", "completed", "in_progress", "pending", "pending", "pending",
                names=DEPOT_TODOS),
          task("vexcel", "No interior scan: pull the EXTERIOR ENTRIES.")),
    calls(task("strategy")), calls(task("critic"))]
DEPOT_PUBLISH = [calls(todos(*["completed"] * 8, names=DEPOT_TODOS), ("publish_brief", {})), AIMessage(content="pass")]


def test_start_pauses_resume_briefs_update_reuses_thread_and_layers(incident, monkeypatch):
    # All three layer pulls must be in flight at once, or the barrier breaks and the pulls fail.
    barrier = threading.Barrier(3, timeout=10)
    real_pull = orchestrator.dossier_mod.pull_layer

    def pull_together(layer, building_id, providers=None):
        barrier.wait()
        return real_pull(layer, building_id, providers)

    monkeypatch.setattr(orchestrator.dossier_mod, "pull_layer", pull_together)

    seen: list[Progress] = []
    session_model = fake_model(*START, *RESUME, *UPDATE)
    started = start_incident("inc_s2", workspace_root=incident.root, on_todos=seen.append, model=session_model)
    assert started.status == "awaiting_confirmation" and started.brief is None
    assert [c.building_id for c in started.candidates] == [BUILDING]
    # What the dispatcher sees, everywhere the candidates go: RunResult, candidates.json, todos.json.
    shown = {"coordinates": {"lat": 34.05, "lon": -118.26}, "scan_available": True,
             "match_reasons": ["address exact match"]}
    assert {k: getattr(started.candidates[0], k) for k in shown} == shown
    assert {k: json.loads(incident.ws.read("candidates.json"))[0][k] for k in shown} == shown
    paused = Progress.model_validate_json(incident.ws.read("todos.json"))
    assert {k: getattr(paused.candidates[0], k) for k in shown} == shown
    assert paused.status == "awaiting_confirmation" and paused.todos[0].status == "in_progress"
    assert not incident.ws.exists("layers")  # nothing happens before the dispatcher answers
    assert orchestrator._incident(incident.ws).confirmed_building_id is None

    with pytest.raises(ValueError, match="not one of the candidates"):
        resume_incident("inc_s2", "some_other_building")
    with pytest.raises(ValueError, match="no brief to update"):
        update_incident("inc_s2", ["x"])

    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.version, v1.fallback_reason) == ("briefed", 1, None)
    assert not barrier.broken and all(incident.ws.exists(f"layers/{name}.json") for name in LAYERS)
    for name in LAYERS:  # each layer subagent's reply, saved by code and labelled unverified
        assert incident.ws.read(f"notes/{name}_summary.md") == note(name, f"{name} summary line [ev_x]")
    assert v1.brief.freshness["prefetched"] is False and v1.brief.freshness["prefetched_at"] is None
    assert v1.brief.published and v1.needs_review == [] and not incident.ws.exists("copilot_questions.json")
    strategy_prompt = session_model.seen["strategy"]  # the strategy subagent is offered both skills
    assert "/skills/occupancy-hazards/SKILL.md" in strategy_prompt and "size-up-format" in strategy_prompt
    assert orchestrator._incident(incident.ws).confirmed_building_id == BUILDING
    assert [c.cross_layer for c in v1.brief.considerations] == [True, False]
    assert v1.critique["verdict"] == "pass" and all(t.status == "completed" for t in v1.todos)
    dossier_written = incident.ws.modified("dossier.json")
    session = orchestrator._SESSIONS["inc_s2"]
    messages_after_v1 = len(session.agent.get_state(session.config).values["messages"])

    with pytest.raises(ValueError, match="not awaiting confirmation"):
        resume_incident("inc_s2", BUILDING)

    v2 = update_incident("inc_s2", incident.replay("c2"))
    assert (v2.status, v2.version, v2.fallback_reason) == ("briefed", 2, None)
    on_disk = Brief.model_validate_json(incident.ws.read("brief_v2.json"))
    assert on_disk.changes_since_last[0] == "Floor contradicted: 3 vs 4 (Call 2)"
    assert "Considerations redrafted: 1 (was 2)" in on_disk.changes_since_last
    assert incident.ws.exists("brief_v1.json") and incident.ws.exists("critique_v2.json")
    assert incident.ws.modified("dossier.json") == dossier_written  # data layers reused
    assert len(session.agent.get_state(session.config).values["messages"]) > messages_after_v1  # same thread
    assert len(v2.todos) == 10 and seen[-1].status == "done" and seen[-1].version == 2
    # The floor contradiction from call 2 reached the dispatcher, as a planned step.
    assert v2.todos[-1].content == "Ask dispatcher: floor contradiction" and v2.brief.published
    [question] = copilot.read_questions(incident.ws)
    assert question.question == "Callers disagree on floor (3 vs 4). Ask: which floor are the flames or smoke on?"


def depot_drafts():
    return [[ROLLER_DOOR, POOL_CHEMICALS]]


def start_depot(depot, *publish_turns):
    start_incident("inc_s4b", workspace_root=depot.root,
                   model=fake_model(*DEPOT_START, *DEPOT_RESUME, *publish_turns, drafts=depot_drafts()))
    depot.eng.confirm(DEPOT)
    save_incident(depot.eng.record, workspace_root=depot.root)
    return resume_incident("inc_s4b", DEPOT)


def test_no_scan_replans_then_pauses_on_a_low_confidence_consideration(depot):
    held = start_depot(depot, *DEPOT_PUBLISH)

    # Re-plan: interior step renamed, exterior entries pulled from the air by the vexcel subagent.
    names = [t.content for t in held.todos]
    assert "No interior scan on file: interior steps skipped" in names and "Matterport interior scan" not in names
    assert names.index("Exterior entries from aerial imagery") == names.index("Vexcel aerial imagery") + 1
    dossier = orchestrator._dossier(depot.ws)
    assert not dossier.scan_available and {"m_qd_ext_roller", "m_qd_ext_front"} <= {m.id for m in dossier.markers}
    assert depot.ws.read("notes/vexcel_summary.md") == note(
        "vexcel", "vexcel summary line [ev_x]", "vexcel exterior entries [ev_x]")

    # Low confidence: the brief exists but is not published; the run is paused at publish_brief.
    assert (held.status, held.needs_review, held.fallback_reason) == ("awaiting_approval", ["c1"], None)
    draft = Brief.model_validate_json(depot.ws.read("brief_v1.json"))
    assert not draft.published and [c.confidence for c in draft.considerations] == [0.4, 0.85]
    assert draft.gaps[:4] == ["Unverified: building.sprinklered", "No interior scan on file",
                              "Interior layout unknown: no stairs, standpipes, elevator or key box locations",
                              "Entries are read from aerial imagery only and are not verified"]
    assert Progress.model_validate_json(depot.ws.read("todos.json")).status == "awaiting_approval"
    assert held.todos[-1].status != "completed"  # "Brief ready" waits for the dispatcher too

    with pytest.raises(ValueError, match="review_brief first"):
        update_incident("inc_s4b", ["x"])
    with pytest.raises(ValueError, match="not considerations of brief v1"):
        review_brief("inc_s4b", drop=["c9"])

    done = review_brief("inc_s4b", drop=["c1"])
    published = Brief.model_validate_json(depot.ws.read("brief_v1.json"))
    assert (done.status, done.needs_review) == ("briefed", []) and published.published
    assert [(c.title, c.rank) for c in published.considerations] == [(POOL_CHEMICALS["title"], 1)]
    assert published.gaps[-1] == "Dispatcher dropped 1 low-confidence consideration(s): c1"
    assert all(t.status == "completed" for t in done.todos)
    with pytest.raises(ValueError, match="no brief awaiting approval"):
        review_brief("inc_s4b")


def test_dispatcher_can_approve_low_confidence_considerations(depot):
    assert start_depot(depot, *DEPOT_PUBLISH).status == "awaiting_approval"
    done = review_brief("inc_s4b")
    assert done.status == "briefed" and done.brief.published
    assert [c.id for c in done.brief.considerations] == ["c1", "c2"] and done.brief.considerations[0].cross_layer


def test_low_confidence_is_held_even_if_the_agent_never_asks_to_publish(depot):
    held = start_depot(depot, calls(todos(*["completed"] * 8, names=DEPOT_TODOS)), AIMessage(content="pass"))
    assert (held.status, held.needs_review) == ("awaiting_approval", ["c1"])
    assert not Brief.model_validate_json(depot.ws.read("brief_v1.json")).published
    done = review_brief("inc_s4b", drop=["c1"])  # nothing is paused: code publishes
    assert done.status == "briefed" and [c.id for c in done.brief.considerations] == ["c2"]


def test_prefetch_then_incident_skips_the_layer_phase(incident, monkeypatch):
    fetched = prefetch_building(BUILDING, model=fake_model(*PREFETCH))
    assert fetched.workspace.location == cache.cache_workspace(BUILDING).location and set(fetched.notes) == set(LAYERS)
    assert fetched.notes["vexcel"] == note("vexcel", "vexcel summary line [ev_x]")
    assert cache.load_fresh(BUILDING).prefetched_at == fetched.prefetched_at

    def no_pull(*a, **k):
        raise AssertionError("a layer was pulled although the building was prefetched")

    monkeypatch.setattr(orchestrator.dossier_mod, "pull_layer", no_pull)
    start_incident("inc_s2", workspace_root=incident.root, model=fake_model(*START, *WARM_RESUME))
    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.fallback_reason) == ("briefed", None)
    assert v1.brief.freshness["prefetched"] is True
    assert v1.brief.freshness["prefetched_at"] == fetched.prefetched_at.isoformat()
    assert [c.cross_layer for c in v1.brief.considerations] == [True, False]
    assert incident.ws.read("notes/matterport_summary.md") == note("matterport", "matterport summary line [ev_x]")
    assert all(incident.ws.exists(f"layers/{name}.json") for name in LAYERS)


def test_prefetch_of_an_unknown_building_fails_before_any_model_call():
    with pytest.raises(FileNotFoundError, match="No CoStar fixture"):
        prefetch_building("no_such_building", model=fake_model())
    assert not cache.cache_dir("no_such_building").exists()


def test_a_paused_incident_survives_a_restart_with_tiger(incident, monkeypatch):
    """With Tiger on, the incidents row and the agent thread outlive the process: after a restart
    the dispatcher's answer still resumes the same paused thread. (Tiger faked in memory here.)"""
    from langgraph.checkpoint.memory import InMemorySaver

    from services.agents import workspace as workspace_mod

    rows, saver = {}, InMemorySaver()
    monkeypatch.setenv("TIGER_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(orchestrator.db, "_execute", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.db, "checkpointer", lambda: saver)
    monkeypatch.setattr(orchestrator.db, "save_incident_state",
                        lambda incident_id, status, candidates, building_id, version, prefetched_at:
                        rows.__setitem__(incident_id, {"status": status, "candidates": candidates,
                                                       "building_id": building_id, "version": version,
                                                       "prefetched_at": prefetched_at}))
    monkeypatch.setattr(orchestrator.db, "load_incident_state", rows.get)
    model = fake_model(*START, *RESUME)
    monkeypatch.setattr(orchestrator, "_model", lambda thinking=True: model)
    if incident.root:  # the restarted process finds the incident by id alone
        monkeypatch.setattr(workspace_mod, "WORKSPACE_ROOT", incident.root)

    start_incident("inc_s2")
    assert rows["inc_s2"]["status"] == "awaiting_confirmation"
    orchestrator._SESSIONS.clear()  # the restart

    incident.eng.confirm(BUILDING)
    v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.fallback_reason) == ("briefed", None)
    assert [c.cross_layer for c in v1.brief.considerations] == [True, False]
    assert rows["inc_s2"]["status"] == "briefed" and rows["inc_s2"]["building_id"] == BUILDING


def test_a_brief_awaiting_approval_survives_a_restart_with_tiger(depot, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from services.agents import workspace as workspace_mod

    rows, saver, saved_briefs = {}, InMemorySaver(), []
    monkeypatch.setenv("TIGER_DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(orchestrator.db, "_execute", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.db, "checkpointer", lambda: saver)
    monkeypatch.setattr(orchestrator.db, "save_incident_state",
                        lambda incident_id, status, candidates, building_id, version, prefetched_at:
                        rows.__setitem__(incident_id, {"status": status, "candidates": candidates,
                                                       "building_id": building_id, "version": version,
                                                       "prefetched_at": prefetched_at}))
    monkeypatch.setattr(orchestrator.db, "load_incident_state", rows.get)
    monkeypatch.setattr(orchestrator.db, "save_brief", lambda brief, *a, **k: saved_briefs.append(brief))
    model = fake_model(*DEPOT_START, *DEPOT_RESUME, *DEPOT_PUBLISH, drafts=depot_drafts())
    monkeypatch.setattr(orchestrator, "_model", lambda thinking=True: model)
    if depot.root:
        monkeypatch.setattr(workspace_mod, "WORKSPACE_ROOT", depot.root)

    start_incident("inc_s4b")
    depot.eng.confirm(DEPOT)
    save_incident(depot.eng.record, workspace_root=depot.root)
    assert resume_incident("inc_s4b", DEPOT).status == "awaiting_approval"
    assert rows["inc_s4b"]["status"] == "awaiting_approval" and saved_briefs == []  # Tiger: published briefs only
    orchestrator._SESSIONS.clear()  # the restart

    done = review_brief("inc_s4b", drop=["c1"])
    assert done.status == "briefed" and [c.id for c in done.brief.considerations] == ["c2"]
    assert rows["inc_s4b"]["status"] == "briefed" and [b.published for b in saved_briefs] == [True]
    assert all(t.status == "completed" for t in done.todos)  # the paused agent turn ran after the restart


def test_a_stalled_model_call_is_retried_once_and_logged_with_the_agent(incident, caplog):
    stall = {"orchestrator": 1}
    with caplog.at_level("WARNING", logger="services.agents.stalls"):
        started = start_incident("inc_s2", workspace_root=incident.root,
                                 model=fake_model(*START, *RESUME, stall=stall))
        assert (started.status, started.model_stalls, started.model_retries) == ("awaiting_confirmation", 1, 1)

        stall.update(strategy=1, vexcel=1)
        incident.eng.confirm(BUILDING)
        v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.status, v1.fallback_reason, v1.model_stalls, v1.model_retries) == ("briefed", None, 2, 2)
    assert v1.critique["verdict"] == "pass" and len(v1.brief.considerations) == 2  # nothing was lost
    messages = [r.getMessage() for r in caplog.records]
    for agent in ("orchestrator", "strategy", "vexcel"):
        assert any(m.startswith(f"Model stall: {agent} got no answer") for m in messages)
        assert any(m.startswith(f"Model retry: {agent} answered on the retry") for m in messages)
    assert orchestrator._SESSIONS["inc_s2"].stalls.by_agent == {"orchestrator": 1, "strategy": 1, "vexcel": 1}


def test_a_second_stall_gives_up_and_the_brief_says_what_was_skipped(incident, caplog):
    start_incident("inc_s2", workspace_root=incident.root, model=fake_model(*START, *RESUME, stall={"critic": 2}))
    incident.eng.confirm(BUILDING)
    with caplog.at_level("WARNING", logger="services.agents.stalls"):
        v1 = resume_incident("inc_s2", BUILDING)
    assert (v1.model_stalls, v1.model_retries) == (2, 1)  # one retry, never a third attempt
    assert any(r.getMessage().startswith("Model stall: critic stalled again on the retry") for r in caplog.records)
    assert v1.status == "briefed" and v1.critique is None
    assert v1.brief.gaps[0] == "Considerations were not checked by the critic"


EAST_BUILDING = {"category": "exposure", "title": "Consider the building to the east",
                 "rationale": "A 3-storey building stands 4.5 m from this mixed-use property.",
                 "evidence_ids": ["ev_aerial_1", "ev_costar_1"]}  # vexcel + costar, no Stair B
STANDPIPE = {**SINGLE_LAYER, "rationale": "A standpipe outlet is on the floor 4 landing of Stair A."}


def test_crew_feedback_is_remembered_at_the_next_incident_in_the_same_building(incident, storage):
    # Incident A: a normal brief. Stair B is in it; the officer finds it bricked up.
    start_incident("inc_s2", workspace_root=incident.root, model=fake_model(*START, *RESUME))
    incident.eng.confirm(BUILDING)
    first = resume_incident("inc_s2", BUILDING)
    assert "m_stair_b" in {m.id for m in first.brief.markers} and first.brief.crew_notes == []
    memory.feedback("inc_s2", "m_stair_b", "wrong", "bricked up at the alley door", workspace_root=incident.root)
    memory.feedback("inc_s2", "building.stories", "confirmed", workspace_root=incident.root)

    # Incident B, later, same building (S1 is a call to 1200 Wilshire Boulevard too).
    later = Incident(storage, "s1")
    later.replay("c1")
    path = f"/memories/{BUILDING}/crew_notes.md"
    with_memory = [
        calls(todos("completed", "in_progress", "in_progress", "in_progress", "pending", "pending", "pending"),
              task("costar"), task("matterport"), task("vexcel")),
        calls(task("strategy", f"Crew memory: {path}")), calls(task("critic")),
        calls(todos(*["completed"] * 7), ("publish_brief", {})), AIMessage(content="pass")]
    model = fake_model(*START, *with_memory, drafts=[[GOOD, STANDPIPE], [EAST_BUILDING, STANDPIPE]])
    start_incident("inc_s1", workspace_root=later.root, model=model)
    later.eng.confirm(BUILDING)
    second = resume_incident("inc_s1", BUILDING)

    # The orchestrator was told about the memory when the building was confirmed ...
    session = orchestrator._SESSIONS["inc_s1"]
    confirm_reply = next(m.text for m in session.agent.get_state(session.config).values["messages"]
                         if m.type == "tool" and m.name == "confirm_building")
    assert f"CREW MEMORY: crews left 2 note(s)" in confirm_reply and path in confirm_reply
    # ... the strategy subagent read it through the /memories/ mount ...
    assert "Crew reported: Stair B is wrong: bricked up at the alley door" in model.seen["memory_read"]
    # ... its first draft, built on Stair B, was refused; the second went through.
    assert (second.status, second.fallback_reason, second.brief.verifier_rejections) == ("briefed", None, 1)
    assert [c.title for c in second.brief.considerations] == [EAST_BUILDING["title"], STANDPIPE["title"]]

    brief = Brief.model_validate_json(later.ws.read("brief_v1.json"))
    assert "m_stair_b" not in {m.id for m in brief.markers} and len(brief.markers) == 9
    assert "m_stair_b" not in {m.id for m in orchestrator._dossier(later.ws).markers}  # nor on the map
    reported = brief.size_up["crew.m_stair_b"]
    assert reported.status == "reported" and reported.value.startswith(
        "Crew reported: Stair B is wrong: bricked up at the alley door (20")
    assert brief.size_up["crew.building.stories"].value.startswith("Crew reported: building.stories = 6 confirmed (")
    assert all(brief.evidence[e].source == "crew" for f in (reported,) for e in f.evidence_ids)
    assert {n.fact_key: n.verdict for n in brief.crew_notes} == {"m_stair_b": "wrong", "building.stories": "correct"}
    assert "m_stair_b" in json.loads(later.ws.read("layers/matterport.json"))["markers"][3]["id"]  # layers stay raw

    # A crew at incident B finds the stair reopened: the next version has it back.
    memory.feedback("inc_s1", "m_stair_b", "confirmed", "reopened", workspace_root=later.root)
    orchestrator._refresh_dossier(session)
    assert "m_stair_b" in {m.id for m in orchestrator._dossier(later.ws).markers}


def test_dispatcher_can_reject_every_candidate(incident):
    start_incident("inc_s2", workspace_root=incident.root,
                   model=fake_model(*START, AIMessage(content="No building confirmed.")))
    result = resume_incident("inc_s2", None)
    assert result.status == "rejected" and result.brief is None
    assert orchestrator._incident(incident.ws).confirmed_building_id is None
    assert not incident.ws.list("brief_v") and not incident.ws.exists("layers")
    assert Progress.model_validate_json(incident.ws.read("todos.json")).status == "rejected"


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
    brief = Brief.model_validate_json(incident.ws.read("brief_v1.json"))
    assert result.status == "briefed" and result.fallback_reason and brief.considerations == []
    assert brief.gaps[0].startswith("Considerations withheld") and brief.location_confirmed
    assert brief.size_up["building.stories"].value == 6 and len(brief.markers) == 10

    v2 = update_incident("inc_s2", incident.replay("c2"))  # updates degrade the same way
    assert v2.fallback_reason and "Floor contradicted: 3 vs 4 (Call 2)" in v2.brief.changes_since_last
    assert v2.brief.published  # facts only: nothing for the dispatcher to review
    # No agent, so nobody planned the copilot question: code still asks the dispatcher.
    assert [q.field for q in copilot.read_questions(incident.ws)] == ["incident.floor"]


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
        brief = Brief.model_validate_json(incident.ws.read(f"brief_v{result.version}.json"))
        assert len(brief.considerations) <= 5
        assert all(c.title.startswith("Consider ") and c.evidence_ids for c in brief.considerations)
        assert all(e in brief.evidence for c in brief.considerations for e in c.evidence_ids)
        assert result.fallback_reason or any(c.cross_layer for c in brief.considerations)
        assert all(t.status == "completed" for t in result.todos)
    assert "Floor contradicted: 3 vs 4 (Call 2)" in v2.brief.changes_since_last
    assert all(incident.ws.exists(f"notes/{name}_summary.md") for name in LAYERS)
    assert incident.ws.exists("notes/strategy_reasoning.md")


# ---------- incident overlay (docs/INCIDENT_OVERLAY_SPEC.md) ----------

from services.agents import overlay as overlay_mod  # noqa: E402
from tests.test_overlay import V1 as OVERLAY_V1, V2 as OVERLAY_V2  # noqa: E402

HOUSE = "fake_cedar_house"  # S5; its plan index is Bruno's plan_index.example.json (cedar_plan_index)
HOUSE_TODOS = ["Confirm building with dispatcher", "CoStar property record", "Matterport interior scan",
               "Vexcel aerial imagery", "Map reports onto the plan", "Draft considerations", "Critique brief",
               "Brief ready", "Update incident overlay (v2)", "Redraft considerations (v2)", "Critique brief v2"]
FRONT_DOOR = {"category": "access", "title": "Consider the front door and the hall stairs to reach upstairs",
              "rationale": "The front door opens to the hall stairs; the driveway is on the east side.",
              "evidence_ids": ["ev_ch_scan_fp_1", "ev_ch_aerial_1"], "refs": ["occ-1"]}  # matterport + vexcel
CHILDREN = {"category": "life_safety", "title": "Consider a search of the upstairs bedrooms for two children",
            "rationale": "A caller reported two children in a back bedroom upstairs.",
            "evidence_ids": ["c1:42.0-51.5"], "refs": ["occ-1", "obj-1"]}


def house_todos(*statuses: str) -> tuple[str, dict]:
    return todos(*statuses, names=HOUSE_TODOS)


HOUSE_START = [calls(house_todos("in_progress", *["pending"] * 7)),
               calls(("confirm_building", {"candidates": [HOUSE]}))]
HOUSE_RESUME = [  # one fan-out turn: three layers and the mapper, in parallel
    calls(house_todos("completed", *["in_progress"] * 4, "pending", "pending", "pending"),
          task("costar"), task("matterport"), task("vexcel"), task("incident_mapper", "Overlay version 1.")),
    calls(house_todos(*["completed"] * 5, "in_progress", "pending", "pending"),
          task("strategy", "Brief version 1, incident overlay at /workspace/overlay_v1.json.")),
    calls(task("critic")), calls(house_todos(*["completed"] * 8), ("publish_brief", {})), AIMessage(content="pass")]
HOUSE_UPDATE = [  # the mapper first: the strategy needs the new overlay
    calls(house_todos(*["completed"] * 8, "in_progress", "pending", "pending"),
          task("incident_mapper", "Update the overlay to version 2.")),
    calls(house_todos(*["completed"] * 9, "in_progress", "pending"),
          task("strategy", "Brief version 2, incident overlay at /workspace/overlay_v2.json.")),
    calls(task("critic")), calls(house_todos(*["completed"] * 11), ("publish_brief", {})), AIMessage(content="pass")]


@pytest.fixture
def house(storage, cedar_plan_index):
    inc = Incident(storage, "s5")
    inc.replay("c1")
    return inc


def overlay_file(inc, version: int) -> dict:
    return json.loads(inc.ws.read(f"overlay_v{version}.json"))


def test_mapper_places_both_calls_on_the_plan_one_overlay_per_brief_version(house, cedar_plan_index, monkeypatch):
    # The mapper reads the plan index while the three layer pulls are still waiting: they run together.
    barrier = threading.Barrier(4, timeout=10)
    real_pull, real_index, waited = orchestrator.dossier_mod.pull_layer, overlay_mod.load_plan_index, []

    def pull_together(layer, building_id, providers=None, **options):
        barrier.wait()
        return real_pull(layer, building_id, providers, **options)

    def index_together(building_id):
        if not waited:
            waited.append(True)
            barrier.wait()
        return real_index(building_id)

    monkeypatch.setattr(orchestrator.dossier_mod, "pull_layer", pull_together)
    monkeypatch.setattr(overlay_mod, "load_plan_index", index_together)

    invented_room = mutated_overlay(OVERLAY_V1, lambda d: d["occupants"][0].update(
        location={"kind": "room", "room_id": "r99"}))
    model = fake_model(*HOUSE_START, *HOUSE_RESUME, *HOUSE_UPDATE,
                       overlays=[invented_room, OVERLAY_V1, OVERLAY_V2],
                       drafts=[[{**CHILDREN, "refs": ["occ-9"]}], [FRONT_DOOR, CHILDREN],
                               [{**FRONT_DOOR, "refs": ["occ-loc-1"]}, {**CHILDREN, "refs": ["occ-loc-1"]}]])
    start_incident("inc_s5", workspace_root=house.root, model=model)
    house.eng.confirm(HOUSE)
    v1 = resume_incident("inc_s5", HOUSE)

    # v1: the invented room was refused, the second submission went through.
    assert not barrier.broken
    assert "r10" in model.seen["plan_index"] and '"number": 2' in model.seen["plan_index"]
    assert "room_id 'r99' does not exist in the plan" in model.seen["overlay_rejections"][0]
    assert (v1.status, v1.fallback_reason, v1.overlay_rejections) == ("briefed", None, 1)
    first = overlay_file(house, 1)
    assert validate_overlay.validate(first, cedar_plan_index) == []
    assert (first["version"], first["model_id"], first["routes"], v1.overlay.version) == (
        1, "fixture-fake_cedar_house", [], v1.brief.version)
    assert first["occupants"][0]["location"] == {"kind": "floor", "floor_reported": 2, "floor_id": "f2"}
    assert first["fire"][0]["confidence"] == 0.6 and first["fire"][0]["location"] == {"kind": "room", "room_id": "r4"}

    # The strategy's considerations point at overlay ids; a ref to nothing was refused.
    assert v1.brief.verifier_rejections == 1
    assert [c.refs for c in v1.brief.considerations] == [["occ-1"], ["occ-1", "obj-1"]]
    span = v1.brief.evidence["c1:42.0-51.5"]  # every overlay evidence id resolves in the brief
    assert (span.kind, span.locator["text"]) == ("transcript_span", "My neighbour's two kids are in the back bedroom upstairs.")
    assert {e for k in ("occupants", "fire", "objectives", "considerations") for x in first[k]
            for e in x["evidence_ids"]} <= set(v1.brief.evidence)

    # Call 2: the mapper runs again, first, and the overlay follows the brief to version 2.
    v2 = update_incident("inc_s5", house.replay("c2"))
    assert "version 2" in model.seen["mapper_tasks"][-1]
    second = overlay_file(house, 2)
    assert validate_overlay.validate(second, cedar_plan_index) == []
    assert (second["version"], v2.brief.version, v2.overlay.version) == (2, 2, 2)
    assert second["contradictions"] == [{"group_id": "occ-loc-1", "key": "occupants.location",
                                         "resolved_by_dispatcher": False}]
    assert {o["id"]: o["status"] for o in second["occupants"]} == {
        "occ-1": "contradicted", "occ-1b": "contradicted", "occ-2": "reported"}
    assert second["changes_since_last"][:2] == ["occ-1 is now contradicted (group occ-loc-1)",
                                                "New: occupants occ-1b (2) on floor 2"]
    assert overlay_file(house, 1)["version"] == 1  # earlier versions stay, for the viewer's slider
    assert [c.refs for c in v2.brief.considerations] == [["occ-loc-1"], ["occ-loc-1"]]
    assert [t.content for t in v2.todos][4] == "Map reports onto the plan" and \
        "Update incident overlay (v2)" in [t.content for t in v2.todos]
    assert overlay_mod.transcript(house.ws)[-1].span_id == "c2:44.0-47.0"  # start_incident kept the transcript


def mutated_overlay(draft: dict, change) -> dict:
    d = json.loads(json.dumps(draft))
    change(d)
    return d


def test_mapper_out_of_tries_gives_an_empty_overlay_that_says_why(house):
    broken = mutated_overlay(OVERLAY_V1, lambda d: d["fire"][0].update(evidence_ids=["ev_call_1"]))
    model = fake_model(*HOUSE_START, *HOUSE_RESUME, overlays=[broken] * 4,
                       drafts=[[FRONT_DOOR], [{**FRONT_DOOR, "refs": []}]])
    start_incident("inc_s5", workspace_root=house.root, model=model)
    house.eng.confirm(HOUSE)
    v1 = resume_incident("inc_s5", HOUSE)
    assert len(model.seen["overlay_rejections"]) == 3 and "budget used up" not in model.seen["overlay_rejections"][-1]
    assert (v1.overlay_rejections, v1.status, v1.fallback_reason) == (3, "briefed", None)
    ov = overlay_file(house, 1)
    assert ov["changes_since_last"] == ["Overlay not available: the incident mapper's 3 submission(s) were all rejected"]
    assert ov["occupants"] == ov["fire"] == [] and ov["version"] == v1.brief.version
    # The strategy may not point at ids of an overlay that does not exist: its refs were refused once.
    assert [c.refs for c in v1.brief.considerations] == [[]] and v1.brief.verifier_rejections == 1


def test_mapper_on_a_building_without_a_scan_uses_floors_only(depot):
    s = Session(incident_id="inc_s4b", ws=depot.ws, candidates=orchestrator.find_candidates(depot.eng.record))
    tools = make_tools(s)
    tools["confirm_building"].invoke({"candidates": [DEPOT], "chosen_building_id": DEPOT})
    assert tools["get_plan_index"].invoke({}).startswith("NO INTERIOR SCAN")
    lines = tools["get_transcript"].invoke({}).splitlines()
    assert lines[1].startswith("c1:2.3-7.6 [caller] There's a fire at the warehouse at 48 Quarry Road")
    fire_in_room = {"fire": [{"id": "fire-1", "kind": "fire", "location": {"kind": "room", "room_id": "r1"},
                              "status": "reported", "confidence": 0.6, "evidence_ids": ["c1:2.3-7.6"]}]}
    reply = tools["submit_overlay"].invoke({"overlay": fire_in_room})
    assert reply.startswith("Rejected") and "room_id 'r1' does not exist in the plan" in reply
    on_floor = mutated_overlay(fire_in_room, lambda d: d["fire"][0].update(location={"kind": "floor", "floor_reported": 1}))
    assert tools["submit_overlay"].invoke({"overlay": on_floor}).startswith("Accepted")
    ov = json.loads(depot.ws.read("overlay_v1.json"))
    assert ov["fire"][0]["location"] == {"kind": "floor", "floor_reported": 1}  # no scan: no floor id
    assert ov["model_id"] == DEPOT  # no Matterport model: the building id
    garbled = tools["submit_overlay"].invoke({"overlay": {"fire": [{"id": "x"}]}})
    assert garbled.startswith("Rejected") and "(0 tries left)" in garbled  # a draft that does not parse counts
    assert tools["submit_overlay"].invoke({"overlay": on_floor}) == "Error: submission budget used up. Do not resubmit."
