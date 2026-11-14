"""Per-incident orchestrator (deepagents).

One persistent deep agent per incident: a checkpointer with thread_id =
incident_id, so the same agent continues from confirmation to brief v1 and on
to v2, v3 ... as new information arrives.

  start_incident   -> plans, then pauses at confirm_building (interrupt_on)
  resume_incident  -> the dispatcher's choice; continues to brief_v1.json
  update_incident  -> new fusion notes; redoes strategy + critic -> brief_vN.json

Subagents and the tools that write the JSON artifacts:

  dossier   load_dossier()            -> dossier.json   + notes/dossier_summary.md
  strategy  submit_considerations()   -> brief_vN.json  + notes/strategy_reasoning*.md
  critic    submit_critique()         -> critique_vN.json (pass | revise)

The model plans, delegates, drafts considerations and critiques. The JSON
artifacts are written only by tools (deterministic, schema-checked); the
built-in filesystem tools can read /workspace/ but write only under
/workspace/notes/.

The todo list is exposed three ways: workspace/<incident_id>/todos.json
(rewritten on every change), the on_todos callback, and RunResult.todos.

Sessions live in this process (InMemorySaver). See docs/agents-api.md.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from flamescout_schemas import (Brief, BuildingCandidate, BuildingDossier, IncidentRecord, Progress,
                                TodoItem, now)

from . import dossier as dossier_mod
from .brief import (ConsiderationDraft, build_brief, check_considerations, merge_evidence,
                    rejected_count)
from .locator import find_candidates

log = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"
INCIDENT, DOSSIER, CANDIDATES, TODOS = "incident.json", "dossier.json", "candidates.json", "todos.json"
MAX_SUBMISSIONS = 3  # per brief version: first draft + 2 regenerations (PRD FR-6)

ORCHESTRATOR_PROMPT = """You are the FlameScout incident orchestrator. Fire calls are coming in and the \
first-arriving crew needs an advisory brief before they arrive. Speed matters: use as few turns as \
possible and batch tool calls exactly as described.

The incident workspace is mounted at /workspace/. You never write files yourself; subagents do that.
Subagents cannot see this conversation, so every task description must be self-contained.

A. FIRST BRIEF. The user message starts with "New incident".
1. First turn, two tool calls together:
   - read_file("/workspace/incident.json", limit=1000)
   - write_todos with five todos: "Confirm building with dispatcher" (in_progress), "Load building
     dossier", "Draft considerations", "Critique brief", "Brief ready" (all pending)
2. Next turn, one tool call only: confirm_building(candidates=[the candidate building ids from the
   user message, in the same order]). The run pauses until the dispatcher answers. If the dispatcher
   rejects the candidates, reply "No building confirmed." and do nothing else.
3. Next turn, two tool calls together: write_todos (confirm completed, dossier in_progress) and
   task(subagent_type="dossier"): ask it to load the dossier for this incident.
4. Next turn, two tool calls together: write_todos (dossier completed, draft in_progress) and
   task(subagent_type="strategy"): ask it to draft brief version 1. Mention any unresolved
   contradiction between callers that you saw in incident.json.
5. Next turn, two tool calls together: write_todos (draft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_v1.json.
6. Only if the critic's verdict is "revise": task the strategy subagent again, quoting the critic's
   issues and saying which revision number this is, then task the critic again. At most 2 revisions.
7. write_todos with everything completed. Then reply with one line: the critic's verdict.

B. UPDATE. The user message starts with "Update": new information arrived after a brief was published,
and the message says which brief version N to produce. The building and its dossier have not changed,
so redo only what the new information affects: never call confirm_building or the dossier subagent.
1. One turn, two tool calls together: write_todos (keep every earlier todo as it is, add "Redraft
   considerations (vN)" in_progress and "Critique brief vN" pending) and task(subagent_type="strategy"):
   ask it to draft brief version N, quoting the new information word for word.
2. Next turn, two tool calls together: write_todos (redraft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_vN.json.
3. Only if the verdict is "revise": same revision loop as A6.
4. write_todos with everything completed. Then reply with one line: the critic's verdict.

Apart from the one read_file in A1, never call read_file, ls, glob or grep: do not re-check the
subagents' work yourself. Never state facts about the building or the incident yourself, and never
contact anyone."""

DOSSIER_PROMPT = """You assemble the building dossier for a confirmed fire incident. Use exactly three turns:
1. Call load_dossier() once. It saves /workspace/dossier.json and returns a digest.
2. Call write_file once to create /workspace/notes/dossier_summary.md: a short markdown summary for the
   crew-brief writer (use and stories, access and egress, hazards, exposures, what is unknown or
   unverified). At most 80 words. Keep the evidence ids in brackets next to each point. Use only
   what the digest says; never guess.
3. Reply with one sentence: what was loaded and what is unknown.
Do not call any other tool."""

STRATEGY_PROMPT = """You draft advisory considerations for the first-arriving fire crew. Your task says \
which brief version N you are drafting. Use exactly three turns:
1. One turn, two tool calls together: read_file("/workspace/incident.json", limit=1000) and
   read_file("/workspace/dossier.json", limit=1000).
2. One turn, two tool calls together:
   - write_file: at most 60 words on what you prioritised and why, what you left out, and anything
     unresolved. Path: /workspace/notes/strategy_reasoning.md for version 1,
     /workspace/notes/strategy_reasoning_v<N>.md for later versions; on a revision add _rev<R>
     before .md.
   - submit_considerations with at most 5 considerations, most important first, each with a
     one-sentence rationale of at most 25 words.
3. Reply with one sentence that includes the brief path submit_considerations returned. If it
   answered "Rejected", fix exactly what it lists and resubmit first.

Rules for every consideration:
- The title starts with "Consider " and is advisory. Never give an order.
- evidence_ids are copied exactly from the keys of the "evidence" objects in the two files, and each
  one actually supports the consideration. No evidence, no consideration.
- Use only facts in the two files. Never guess or add outside knowledge about this building.
- Caller reports are unconfirmed: say "reported". Where callers contradict each other, do not pick a
  side; word the consideration so it holds for both, or say it is unresolved.
- Do not build a consideration on a fact whose status is "unverified"."""

CRITIC_PROMPT = """You are the critic for a fire-crew advisory brief. Use exactly three turns:
1. read_file(<the brief path given in your task>, limit=3000).
2. Call submit_critique once.
3. Reply with the verdict and, for "revise", the issues, in one or two sentences.

Check every item in "considerations" against the brief's own "size_up", "markers", "hazards",
"exposures", "gaps" and "evidence":
- The wording is advisory ("Consider ..."), never an order.
- Each cited evidence id supports the title and rationale: find the facts or markers that cite it.
- Nothing is stated that the brief does not contain. No invented numbers, locations or tenants.
- Caller reports are described as reported, and a contradiction listed in "gaps" is not silently
  resolved in favour of one caller.
- Nothing relies on a fact whose status is "unverified".
verdict "pass" only if every consideration passes. Otherwise "revise", with one specific issue per
problem that names the consideration id (c1, c2, ...). Do not ask for stylistic changes."""

REJECT_MESSAGE = ("The dispatcher says none of the candidates is the right building. Stop here: do not "
                  "confirm a building, load a dossier or draft a brief.")

Status = Literal["awaiting_confirmation", "briefed", "rejected"]


@dataclass
class RunResult:
    incident_id: str
    status: Status
    version: int                         # brief version this call worked on
    brief: Optional[Brief]               # None unless status == "briefed"
    candidates: list[BuildingCandidate]
    todos: list[TodoItem]
    critique: Optional[dict]
    fallback_reason: Optional[str]       # set when the brief is facts-only
    seconds: float                       # wall clock of this call
    trace_run_id: Optional[str]          # LangSmith run id of this call, None when tracing is off


@dataclass
class Session:
    """One incident's persistent agent and the state its tools share."""
    incident_id: str
    ws: Path
    candidates: list[BuildingCandidate] = field(default_factory=list)
    status: Status = "awaiting_confirmation"
    version: int = 1
    accepted: int = 0      # accepted submissions for the current version
    rejections: int = 0    # draft considerations rejected for the current version
    agent: Any = None
    error: Optional[str] = None
    todos: list[TodoItem] = field(default_factory=list)
    on_todos: Optional[Callable[[Progress], None]] = None
    trace_run_id: Optional[str] = None

    @property
    def brief_path(self) -> Path:
        return self.ws / f"brief_v{self.version}.json"

    @property
    def critique_path(self) -> Path:
        return self.ws / f"critique_v{self.version}.json"

    @property
    def config(self) -> dict:
        return {"configurable": {"thread_id": self.incident_id}}


_SESSIONS: dict[str, Session] = {}
_CHECKPOINTER = InMemorySaver()  # in-process: a restart loses paused incidents


# ---------- workspace helpers ----------

def _write(path: Path, text: str) -> None:
    """Atomic, so the UI never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _incident(ws: Path) -> IncidentRecord:
    return IncidentRecord.model_validate_json((ws / INCIDENT).read_text())


def _dossier(ws: Path) -> BuildingDossier:
    return BuildingDossier.model_validate_json((ws / DOSSIER).read_text())


def _confirm(ws: Path, building_id: str) -> None:
    incident = _incident(ws)
    incident.confirmed_building_id, incident.status = building_id, "confirmed"
    _write(ws / INCIDENT, incident.model_dump_json(indent=2))


def _write_brief(s: Session, drafts: list[ConsiderationDraft], extra_gaps: tuple[str, ...] = ()) -> Brief:
    prev_path = s.ws / f"brief_v{s.version - 1}.json"
    previous = Brief.model_validate_json(prev_path.read_text()) if prev_path.exists() else None
    brief = build_brief(_incident(s.ws), _dossier(s.ws), drafts, version=s.version, extra_gaps=extra_gaps,
                        previous=previous, verifier_rejections=s.rejections)
    _write(s.brief_path, brief.model_dump_json(indent=2))
    return brief


def _publish(s: Session, progress_status: str) -> None:
    progress = Progress(incident_id=s.incident_id, status=progress_status, version=s.version,
                        candidates=s.candidates, todos=s.todos, trace_run_id=s.trace_run_id)
    _write(s.ws / TODOS, progress.model_dump_json(indent=2))
    if s.on_todos:
        s.on_todos(progress)


# ---------- tools (the only writers of the JSON artifacts) ----------

def make_tools(s: Session) -> dict[str, BaseTool]:
    ws = s.ws

    @tool(parse_docstring=True)
    def confirm_building(candidates: list[str], chosen_building_id: str = "") -> str:
        """Ask the dispatcher which building this incident is at. The run pauses until they answer.

        Args:
            candidates: Candidate building ids from the user message, best match first.
            chosen_building_id: Leave empty. The dispatcher fills it in.
        """
        known = [c.building_id for c in s.candidates]
        if chosen_building_id not in known:
            return f"Error: the dispatcher has not chosen one of {known}."
        _confirm(ws, chosen_building_id)
        return f"The dispatcher confirmed building {chosen_building_id}."

    @tool
    def load_dossier() -> str:
        """Load the dossier for this incident's confirmed building and save it as /workspace/dossier.json.

        Returns a digest of the dossier with the evidence ids for every fact and marker.
        """
        building_id = _incident(ws).confirmed_building_id
        if not building_id:
            return "Error: the dispatcher has not confirmed a building yet."
        if (ws / DOSSIER).exists() and (d := _dossier(ws)).building_id == building_id:
            return "Already loaded, nothing rewritten.\n" + dossier_mod.digest(d)
        try:
            d = dossier_mod.load_dossier(building_id)
        except (OSError, ValueError) as e:
            return f"Error: {e}"
        _write(ws / DOSSIER, d.model_dump_json(indent=2))
        return dossier_mod.digest(d)

    @tool(parse_docstring=True)
    def submit_considerations(considerations: list[ConsiderationDraft]) -> str:
        """Submit the ranked considerations. If they pass the checks, the brief for this version is written.

        Args:
            considerations: At most 5, most important first. Each title starts with "Consider ".
        """
        if s.accepted >= MAX_SUBMISSIONS:
            return "Error: regeneration budget used up. Do not resubmit."
        if not (ws / DOSSIER).exists():
            return "Error: /workspace/dossier.json does not exist yet. The dossier subagent must run first."
        evidence = merge_evidence(_incident(ws), _dossier(ws))
        if errors := check_considerations(considerations, evidence):
            s.rejections += rejected_count(considerations, evidence)
            return "Rejected, nothing written. Fix these and resubmit:\n- " + "\n- ".join(errors)
        _write_brief(s, considerations)
        s.critique_path.unlink(missing_ok=True)  # any earlier critique is about an older draft
        s.accepted += 1
        return (f"Accepted: /workspace/{s.brief_path.name} written with "
                f"{len(considerations)} considerations.")

    @tool(parse_docstring=True)  # not return_direct: task() would hand the orchestrator an empty result
    def submit_critique(verdict: Literal["pass", "revise"], issues: list[str]) -> str:
        """Record the verdict on the current brief version.

        Args:
            verdict: "pass" if every consideration is advisory and supported, otherwise "revise".
            issues: One specific issue per problem, naming the consideration id. Empty for "pass".
        """
        if not s.brief_path.exists():
            return f"Error: /workspace/{s.brief_path.name} does not exist yet."
        if verdict == "revise" and not issues:
            return 'Error: "revise" needs at least one issue.'
        if verdict == "revise":
            s.rejections += len(issues)
        _write(s.critique_path, json.dumps(
            {"brief_id": f"{s.incident_id}_v{s.version}", "verdict": verdict, "issues": issues,
             "checked_at": now().isoformat()}, indent=2))
        return f"Recorded verdict: {verdict}."

    return {t.name: t for t in (confirm_building, load_dossier, submit_considerations, submit_critique)}


# ---------- agent ----------

def _model(thinking: bool = True):
    from langchain_google_genai import ChatGoogleGenerativeAI

    # "low" is the lowest thinking level this model accepts ("minimal" is a 400) and default
    # thinking roughly doubles every turn. thinking_budget=0 switches thinking off altogether.
    effort = {"thinking_level": "low"} if thinking else {"thinking_budget": 0}
    return ChatGoogleGenerativeAI(model=os.environ["GEMINI_FAST_MODEL"],
                                  google_api_key=os.environ["GEMINI_API_KEY"], **effort)


def build_orchestrator(s: Session, model: Any = None, checkpointer: Any = None):
    tools = make_tools(s)
    subagents = [
        {"name": "dossier",
         "description": "Loads the confirmed building's dossier into the workspace and summarises it.",
         "system_prompt": DOSSIER_PROMPT, "tools": [tools["load_dossier"]]},
        {"name": "strategy",
         "description": "Drafts up to 5 advisory considerations from incident.json and dossier.json and "
                        "writes the brief for the version it is told. Can revise given critic issues.",
         "system_prompt": STRATEGY_PROMPT, "tools": [tools["submit_considerations"]]},
        {"name": "critic",
         "description": "Checks a brief file: advisory wording, evidence support. Returns pass or revise.",
         "system_prompt": CRITIC_PROMPT, "tools": [tools["submit_critique"]],
         # Even at "low" the critic sometimes spent 1000+ reasoning tokens (13 s) on one turn.
         "model": model or _model(thinking=False)},
    ]
    return create_deep_agent(
        model=model or _model(),
        tools=[tools["confirm_building"]],
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware()],  # write_todos is opt-in in deepagents 0.7
        subagents=subagents,
        # Only /workspace/ is real disk; offloaded tool results etc. stay in ephemeral state.
        backend=CompositeBackend(default=StateBackend(),
                                 routes={"/workspace/": FilesystemBackend(root_dir=s.ws, virtual_mode=True)}),
        # First match wins: the model may write notes, nothing else in the workspace.
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/workspace/notes/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="deny"),
        ],
        # The dispatcher picks the building: the answer is an "edit" that fills chosen_building_id.
        interrupt_on={"confirm_building": {"allowed_decisions": ["edit", "reject"]}},
        checkpointer=checkpointer or _CHECKPOINTER,
        name="flamescout-orchestrator",
    )


def _enable_tracing() -> bool:
    if not os.environ.get("LANGSMITH_API_KEY"):
        log.warning("LangSmith tracing is OFF: LANGSMITH_API_KEY is not set")
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", "flamescout")
    return os.environ["LANGSMITH_TRACING"].lower() == "true"


def flush_traces() -> None:
    """Block until queued LangSmith runs are sent. Call before a short-lived process exits."""
    from langchain_core.tracers.langchain import wait_for_all_tracers

    wait_for_all_tracers()


def _log_activity(ns: tuple, update: dict, t0: float) -> None:
    who = "subagent" if ns else "main"
    messages = update.get("messages") or []
    messages = getattr(messages, "value", messages)
    for m in messages if isinstance(messages, list) else [messages]:
        for call in getattr(m, "tool_calls", None) or []:
            args = call["args"]
            hint = args.get("subagent_type") or args.get("file_path") or ""
            log.info("%5.1fs %-8s -> %s(%s)", time.perf_counter() - t0, who, call["name"], hint)


def _pending_confirmation(s: Session) -> Optional[dict]:
    """The confirm_building call waiting for the dispatcher, if the run is paused on one."""
    if s.agent is None:
        return None
    for interrupt in s.agent.get_state(s.config).interrupts:
        for action in interrupt.value.get("action_requests", []):
            if action["name"] == "confirm_building":
                return action
    return None


def _run(s: Session, agent_input: Any, phase: str, t0: float) -> None:
    """Stream one leg of the incident's thread. Never raises: failures land in s.error."""
    run_id = uuid.uuid4()
    s.trace_run_id = str(run_id) if _enable_tracing() else None
    s.error = None
    _publish(s, "running")
    try:
        if s.agent is None:
            raise RuntimeError("the orchestrator agent could not be built")
        config = {**s.config, "run_id": run_id, "run_name": f"incident:{s.incident_id}:{phase}",
                  "recursion_limit": 60, "tags": ["flamescout", "orchestrator", phase],
                  "metadata": {"incident_id": s.incident_id, "thread_id": s.incident_id,
                               "brief_version": s.version}}
        for chunk in s.agent.stream(agent_input, config, stream_mode="updates", subgraphs=True, version="v2"):
            if chunk["type"] != "updates":
                continue
            for update in chunk["data"].values():
                if not isinstance(update, dict):
                    continue  # e.g. the __interrupt__ entry; read back from the checkpoint instead
                _log_activity(chunk["ns"], update, t0)
                if not chunk["ns"] and "todos" in update:  # the orchestrator's own plan
                    s.todos = [TodoItem(**t) for t in update["todos"]]
                    _publish(s, "running")
    except Exception as e:  # the crew still gets the facts
        log.exception("Orchestrator %s failed for %s", phase, s.incident_id)
        s.error = f"the orchestrator run failed ({type(e).__name__})"


def _result(s: Session, t0: float, brief: Optional[Brief] = None, critique: Optional[dict] = None,
            fallback_reason: Optional[str] = None) -> RunResult:
    _publish(s, "done" if s.status == "briefed" else s.status)
    return RunResult(incident_id=s.incident_id, status=s.status, version=s.version, brief=brief,
                     candidates=s.candidates, todos=s.todos, critique=critique,
                     fallback_reason=fallback_reason, seconds=time.perf_counter() - t0,
                     trace_run_id=s.trace_run_id)


def _finish_brief(s: Session, t0: float) -> RunResult:
    """Settle the current version: keep the agent's brief, or fall back to facts only (PRD FR-6)."""
    critique = json.loads(s.critique_path.read_text()) if s.critique_path.exists() else None
    fallback_reason: Optional[str] = None
    if not s.brief_path.exists():
        fallback_reason = s.error or "the strategy agent produced no accepted considerations"
    elif critique and critique["verdict"] == "revise":
        fallback_reason = "the critic still flagged issues: " + "; ".join(critique["issues"])

    if fallback_reason:
        if not (s.ws / DOSSIER).exists():
            d = dossier_mod.load_dossier(_incident(s.ws).confirmed_building_id or "")
            _write(s.ws / DOSSIER, d.model_dump_json(indent=2))
        brief = _write_brief(s, [], extra_gaps=(f"Considerations withheld: {fallback_reason}",))
    else:
        brief = Brief.model_validate_json(s.brief_path.read_text())
        if critique is None:
            brief.gaps.insert(0, "Considerations were not checked by the critic")
            _write(s.brief_path, brief.model_dump_json(indent=2))
    s.status = "briefed"
    return _result(s, t0, brief, critique, fallback_reason)


def _session(incident_id: str) -> Session:
    if incident_id not in _SESSIONS:
        raise ValueError(f"No session for incident {incident_id}: call start_incident first "
                         "(sessions do not survive a process restart)")
    return _SESSIONS[incident_id]


# ---------- public API (see docs/agents-api.md) ----------

def start_incident(incident_id: str, workspace_root: Path = WORKSPACE,
                   on_todos: Optional[Callable[[Progress], None]] = None,
                   model: Any = None, checkpointer: Any = None) -> RunResult:
    """Start the incident's agent. It plans, then pauses until the dispatcher confirms a building."""
    t0 = time.perf_counter()
    ws = Path(workspace_root) / incident_id
    candidates = find_candidates(_incident(ws))
    if not candidates:
        raise ValueError(f"Incident {incident_id} has no candidate buildings yet: "
                         "callers gave no location that matches a known building")

    for stale in [ws / DOSSIER, ws / CANDIDATES, ws / TODOS,
                  *ws.glob("brief_v*.json"), *ws.glob("critique_v*.json")]:
        stale.unlink(missing_ok=True)
    shutil.rmtree(ws / "notes", ignore_errors=True)
    _write(ws / CANDIDATES, json.dumps([c.model_dump() for c in candidates], indent=2))

    saver = checkpointer or _CHECKPOINTER
    saver.delete_thread(incident_id)  # one thread per incident; starting again starts it clean
    s = _SESSIONS[incident_id] = Session(incident_id=incident_id, ws=ws, candidates=candidates,
                                         on_todos=on_todos)
    try:
        s.agent = build_orchestrator(s, model, saver)
    except Exception:
        log.exception("Could not build the orchestrator for %s", incident_id)

    listing = "\n".join(f"- {c.building_id}: {c.name}, {c.address} (match {c.score})" for c in candidates)
    prompt = f"New incident {incident_id}. Candidate buildings, best match first:\n{listing}"
    _run(s, {"messages": [{"role": "user", "content": prompt}]}, "start", t0)
    if not _pending_confirmation(s):
        # Still hand the dispatcher the candidates; resume_incident then confirms without the agent.
        s.error = s.error or "the orchestrator did not pause for confirmation"
        log.warning("Incident %s: %s", incident_id, s.error)
    return _result(s, t0)


def resume_incident(incident_id: str, building_id: Optional[str]) -> RunResult:
    """The dispatcher's answer: a candidate's building_id, or None for "none of these"."""
    t0 = time.perf_counter()
    s = _session(incident_id)
    if s.status != "awaiting_confirmation":
        raise ValueError(f"Incident {incident_id} is not awaiting confirmation (status: {s.status})")
    if building_id is not None and building_id not in [c.building_id for c in s.candidates]:
        raise ValueError(f"{building_id!r} is not one of the candidates for incident {incident_id}")

    pending = _pending_confirmation(s)
    if building_id is None:
        if pending:
            _run(s, Command(resume={"decisions": [{"type": "reject", "message": REJECT_MESSAGE}]}),
                 "reject", t0)
        s.status = "rejected"
        return _result(s, t0)

    if pending:
        # Built here, not taken from the caller: the tool name and candidate list stay pinned.
        edit = {"type": "edit", "edited_action": {
            "name": "confirm_building",
            "args": {"candidates": pending["args"].get("candidates", []),
                     "chosen_building_id": building_id}}}
        _run(s, Command(resume={"decisions": [edit]}), "resume", t0)
    # else: start_incident never reached the gate; s.error says why and becomes the fallback reason
    if _incident(s.ws).confirmed_building_id != building_id:
        _confirm(s.ws, building_id)  # the agent never got there; the dispatcher's choice still stands
    return _finish_brief(s, t0)


def update_incident(incident_id: str, notes: list[str]) -> RunResult:
    """New information after a brief (fusion change notes). Same thread; writes the next brief version."""
    t0 = time.perf_counter()
    s = _session(incident_id)
    if s.status != "briefed":
        raise ValueError(f"Incident {incident_id} has no brief to update yet (status: {s.status})")
    s.version, s.accepted, s.rejections = s.version + 1, 0, 0
    listing = "\n".join(f"- {n}" for n in notes)
    prompt = (f"Update for incident {incident_id}. /workspace/incident.json has changed. "
              f"New information from the calls:\n{listing}\n"
              f"Produce brief version {s.version}: /workspace/brief_v{s.version}.json.")
    _run(s, {"messages": [{"role": "user", "content": prompt}]}, "update", t0)
    return _finish_brief(s, t0)
