"""Per-incident orchestrator (deepagents).

One persistent deep agent per incident: a checkpointer with thread_id =
incident_id, so the same agent continues from confirmation to brief v1 and on
to v2, v3 ... as new information arrives.

  start_incident   -> plans, then pauses at confirm_building (interrupt_on)
  resume_incident  -> the dispatcher's choice; continues to brief_v1.json
  update_incident  -> new fusion notes; redoes strategy + critic -> brief_vN.json

Subagents and the tools that write the JSON artifacts:

  costar      pull_costar()      -> layers/costar.json
  matterport  pull_matterport()  -> layers/matterport.json
  vexcel      pull_vexcel()      -> layers/vexcel.json
              (delegated in ONE orchestrator turn, so the three run concurrently; each makes two
               model calls: pull, then a short summary that code saves as notes/<layer>_summary.md;
               when the third layer lands, code merges them into dossier.json)
  strategy    submit_considerations()   -> brief_vN.json  + notes/strategy_reasoning*.md
  critic      submit_critique()         -> critique_vN.json (pass | revise)

The model plans, delegates, drafts considerations and critiques. The JSON
artifacts are written only by tools (deterministic, schema-checked); the
built-in filesystem tools can read /workspace/ but write only under
/workspace/notes/.

The todo list is exposed three ways: todos.json in the workspace (rewritten on every
change), the on_todos callback, and RunResult.todos.

prefetch_building() runs the same three layer subagents ahead of time into the
building's prefetch cache (cache.py). An incident at a building with a cache under
24 h old copies it in at confirmation and skips the layer phase.

Storage (see workspace.py and db.py):
  - the workspace (the documents above) is MongoDB Atlas when MONGODB_URI is set,
    else local files under workspace/<incident_id>/
  - incidents, facts, call segments, published briefs, timings and the agent threads
    (PostgresSaver) are in Postgres on Tiger Data when TIGER_DATABASE_URL is set, so an
    incident survives a restart. Without it, threads live in this process (InMemorySaver).
See docs/agents-api.md.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.tools import BaseTool, StructuredTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from flamescout_schemas import (Brief, BuildingCandidate, BuildingDossier, CallSegment, IncidentRecord,
                                Progress, TodoItem, now)

from . import cache, db
from . import dossier as dossier_mod
from .brief import (ConsiderationDraft, build_brief, check_considerations, merge_evidence,
                    rejected_count)
from .dossier import LAYERS, LayerData
from .locator import find_candidates
from .workspace import WORKSPACE_ROOT, Workspace, incident_workspace

log = logging.getLogger(__name__)

WORKSPACE = WORKSPACE_ROOT
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
   - write_todos with seven todos: "Confirm building with dispatcher" (in_progress), "CoStar property
     record", "Matterport interior scan", "Vexcel aerial imagery", "Draft considerations",
     "Critique brief", "Brief ready" (all pending)
2. Next turn, one tool call only: confirm_building(candidates=[the candidate building ids from the
   user message, in the same order]). The run pauses until the dispatcher answers. If the dispatcher
   rejects the candidates, reply "No building confirmed." and do nothing else.
3. If confirm_building answered that the building data was PREFETCHED, skip this step: the data is
   already in the workspace, so never call the costar, matterport or vexcel subagents, and treat the
   three data todos as completed in step 4.
   Otherwise, next turn, FOUR tool calls together, never one at a time: write_todos (confirm completed,
   the three data todos in_progress) and task(subagent_type="costar"), task(subagent_type="matterport"),
   task(subagent_type="vexcel"), each asked to pull its data layer for this incident. Calling all
   three in the same turn makes them run in parallel.
4. Next turn, two tool calls together: write_todos (the three data todos completed, draft
   in_progress) and task(subagent_type="strategy"): ask it to draft brief version 1. Mention any unresolved
   contradiction between callers that you saw in incident.json.
5. Next turn, two tool calls together: write_todos (draft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_v1.json.
6. Only if the critic's verdict is "revise": task the strategy subagent again, quoting the critic's
   issues and saying which revision number this is, then task the critic again. At most 2 revisions.
7. write_todos with everything completed. Then reply with one line: the critic's verdict.

B. UPDATE. The user message starts with "Update": new information arrived after a brief was published,
and the message says which brief version N to produce. The building and its dossier have not changed,
so redo only what the new information affects: never call confirm_building or the costar,
matterport or vexcel subagents.
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

LAYER_PROMPT = """You pull one data layer of a building dossier for fire crews: {what}. \
Use exactly two turns:
1. Call pull_{layer}() once. It saves the layer and returns a digest.
2. Reply with a summary of 2 to 4 short lines: what this layer tells a fire crew ({focus}) and what
   is missing or unverified. Keep the evidence ids in brackets next to each point. Use only what the
   digest says; never guess. Your reply is saved as this layer's note, so write nothing else.
Do not call any other tool."""

PREFETCH_PROMPT = """You prefetch building data before any incident. In ONE turn make three tool calls \
together: task(subagent_type="costar"), task(subagent_type="matterport"), task(subagent_type="vexcel"), each \
asked to pull its data layer for the building in the user message. Then reply "done". Never call them \
one at a time, and call nothing else."""

LAYERS_INFO = {
    "costar": ("the CoStar property record", "use, stories, tenants, tenant-related hazards"),
    "matterport": ("the Matterport interior scan", "entrances, stairs, elevator, key box, FDC, standpipes"),
    "vexcel": ("Vexcel aerial imagery", "approach and rear access, roof, adjacent structures"),
}

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
- Do not build a consideration on a fact whose status is "unverified".
- At least one consideration must combine two data layers: its evidence_ids include evidence from two
  different sources among costar, matterport and vexcel (the "source" field of each evidence item),
  for example an aerial access point together with the interior stair it leads to. Only combine
  layers where both pieces of evidence really support the consideration."""

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
- Where "cross_layer" is true, the pieces of evidence from different sources are about the same place
  or route and fit together physically.
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
    ws: Workspace
    candidates: list[BuildingCandidate] = field(default_factory=list)
    status: Status = "awaiting_confirmation"
    building_id: Optional[str] = None  # what the dispatcher confirmed
    version: int = 1
    accepted: int = 0      # accepted submissions for the current version
    rejections: int = 0    # draft considerations rejected for the current version
    agent: Any = None
    error: Optional[str] = None
    todos: list[TodoItem] = field(default_factory=list)
    on_todos: Optional[Callable[[Progress], None]] = None
    trace_run_id: Optional[str] = None
    lock: Any = field(default_factory=threading.RLock)  # the three layer tools run concurrently
    use_cache: bool = True                     # reuse prefetched layers (data/cache) when fresh
    prefetched_at: Optional[datetime] = None   # set when the layers came from the cache
    publish: bool = True                       # write todos.json / call on_todos (off for prefetch)
    layer_tasks: dict = field(default_factory=dict)  # task tool_call_id -> layer, to save its reply
    start_seconds: float = 0.0                 # start_incident's wall clock, for time-to-brief

    @staticmethod
    def layer_rel(layer: str) -> str:
        return f"layers/{layer}.json"

    @property
    def brief_rel(self) -> str:
        return f"brief_v{self.version}.json"

    @property
    def critique_rel(self) -> str:
        return f"critique_v{self.version}.json"

    @property
    def config(self) -> dict:
        return {"configurable": {"thread_id": self.incident_id}}


_SESSIONS: dict[str, Session] = {}
_MEMORY_SAVER = InMemorySaver()  # without Tiger: in-process, a restart loses paused incidents


def _checkpointer():
    return db.checkpointer() if db.enabled() else _MEMORY_SAVER


# ---------- workspace helpers ----------

def _incident(ws: Workspace) -> IncidentRecord:
    return IncidentRecord.model_validate_json(ws.read(INCIDENT))


def _dossier(ws: Workspace) -> BuildingDossier:
    return BuildingDossier.model_validate_json(ws.read(DOSSIER))


def _confirm(ws: Workspace, building_id: str) -> None:
    incident = _incident(ws)
    incident.confirmed_building_id, incident.status = building_id, "confirmed"
    ws.write(INCIDENT, incident.model_dump_json(indent=2))


def _building_id(s: Session) -> str:
    return s.building_id or _incident(s.ws).confirmed_building_id or ""


def _layer_on_disk(s: Session, layer: str, building_id: str) -> Optional[LayerData]:
    text = s.ws.read_or_none(s.layer_rel(layer))
    if text is None:
        return None
    data = LayerData.model_validate_json(text)
    return data if data.building_id == building_id else None


def _pull(s: Session, layer: str) -> str:
    """Tool body for pull_<layer>: save the layer, merge into dossier.json once all three are in."""
    building_id = _building_id(s)
    if not building_id:
        return "Error: the dispatcher has not confirmed a building yet."
    if data := _layer_on_disk(s, layer, building_id):
        return "Already pulled, nothing rewritten.\n" + dossier_mod.digest(data)
    try:
        data = dossier_mod.pull_layer(layer, building_id)
    except (OSError, ValueError, KeyError) as e:
        return f"Error: {e}"
    with s.lock:
        s.ws.write(s.layer_rel(layer), data.model_dump_json(indent=2))
        layers = [_layer_on_disk(s, name, building_id) for name in LAYERS]
        if all(layers):
            s.ws.write(DOSSIER, dossier_mod.merge_layers(building_id, layers).model_dump_json(indent=2))
    return dossier_mod.digest(data)


def _load_prefetched(s: Session) -> bool:
    """Copy a fresh prefetch cache for the confirmed building into the workspace and merge it."""
    building_id = _building_id(s)
    hit = cache.load_fresh(building_id) if s.use_cache and building_id else None
    if hit is None:
        return False
    with s.lock:
        for name, data in hit.layers.items():
            s.ws.write(s.layer_rel(name), data.model_dump_json(indent=2))
        for name, note in hit.notes.items():
            s.ws.write(f"notes/{name}_summary.md", note)
        dossier = dossier_mod.merge_layers(building_id, hit.layers.values())
        s.ws.write(DOSSIER, dossier.model_dump_json(indent=2))
        s.prefetched_at = hit.prefetched_at
    log.info("Incident %s: using data layers prefetched at %s", s.incident_id, hit.prefetched_at.isoformat())
    return True


def _ensure_dossier(s: Session) -> None:
    """dossier.json must exist before a brief is built: pull in code whatever a subagent did not."""
    building_id = _building_id(s)
    with s.lock:
        if s.ws.exists(DOSSIER) and _dossier(s.ws).building_id == building_id:
            return
        if not any(_layer_on_disk(s, name, building_id) for name in LAYERS) and _load_prefetched(s):
            return
        layers = []
        for name in LAYERS:
            data = _layer_on_disk(s, name, building_id)
            if data is None:
                log.warning("Incident %s: %s layer was not pulled by its subagent; pulling it in code",
                            s.incident_id, name)
                data = dossier_mod.pull_layer(name, building_id)
                s.ws.write(s.layer_rel(name), data.model_dump_json(indent=2))
            layers.append(data)
        s.ws.write(DOSSIER, dossier_mod.merge_layers(building_id, layers).model_dump_json(indent=2))


def _write_brief(s: Session, drafts: list[ConsiderationDraft], extra_gaps: tuple[str, ...] = ()) -> Brief:
    prev_text = s.ws.read_or_none(f"brief_v{s.version - 1}.json")
    previous = Brief.model_validate_json(prev_text) if prev_text else None
    brief = build_brief(_incident(s.ws), _dossier(s.ws), drafts, version=s.version, extra_gaps=extra_gaps,
                        previous=previous, verifier_rejections=s.rejections, prefetched_at=s.prefetched_at)
    s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
    return brief


def _publish(s: Session, progress_status: str) -> None:
    if not s.publish:
        return
    progress = Progress(incident_id=s.incident_id, status=progress_status, version=s.version,
                        candidates=s.candidates, todos=s.todos, trace_run_id=s.trace_run_id)
    s.ws.write(TODOS, progress.model_dump_json(indent=2))
    if s.on_todos:
        s.on_todos(progress)


def _save_state(s: Session) -> None:
    """The incidents row in Tiger: what _session() needs to pick the incident up after a restart."""
    if s.publish:
        db.save_incident_state(s.incident_id, s.status, [c.model_dump() for c in s.candidates],
                               s.building_id, s.version, s.prefetched_at)


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
        s.building_id = chosen_building_id
        if _load_prefetched(s):
            return (f"The dispatcher confirmed building {chosen_building_id}. Its building data was "
                    "PREFETCHED and is already in /workspace/dossier.json: skip the costar, matterport "
                    "and vexcel subagents and go straight to the strategy step.")
        return f"The dispatcher confirmed building {chosen_building_id}. No prefetched data: pull the three layers."

    def pull_tool(layer: str) -> BaseTool:
        def pull() -> str:
            return _pull(s, layer)

        return StructuredTool.from_function(
            pull, name=f"pull_{layer}",
            description=f"Pull {LAYERS_INFO[layer][0]} for the building and save it as "
                        f"layers/{layer}.json. Returns a digest with the evidence ids.")

    @tool(parse_docstring=True)
    def submit_considerations(considerations: list[ConsiderationDraft]) -> str:
        """Submit the ranked considerations. If they pass the checks, the brief for this version is written.

        Args:
            considerations: At most 5, most important first. Each title starts with "Consider ".
        """
        if s.accepted >= MAX_SUBMISSIONS:
            return "Error: regeneration budget used up. Do not resubmit."
        if not _building_id(s):
            return "Error: the dispatcher has not confirmed a building yet."
        _ensure_dossier(s)
        evidence = merge_evidence(_incident(ws), _dossier(ws))
        if errors := check_considerations(considerations, evidence):
            s.rejections += rejected_count(considerations, evidence)
            return "Rejected, nothing written. Fix these and resubmit:\n- " + "\n- ".join(errors)
        _write_brief(s, considerations)
        ws.delete(s.critique_rel)  # any earlier critique is about an older draft
        s.accepted += 1
        return (f"Accepted: /workspace/{s.brief_rel} written with "
                f"{len(considerations)} considerations.")

    @tool(parse_docstring=True)  # not return_direct: task() would hand the orchestrator an empty result
    def submit_critique(verdict: Literal["pass", "revise"], issues: list[str]) -> str:
        """Record the verdict on the current brief version.

        Args:
            verdict: "pass" if every consideration is advisory and supported, otherwise "revise".
            issues: One specific issue per problem, naming the consideration id. Empty for "pass".
        """
        if not ws.exists(s.brief_rel):
            return f"Error: /workspace/{s.brief_rel} does not exist yet."
        if verdict == "revise" and not issues:
            return 'Error: "revise" needs at least one issue.'
        if verdict == "revise":
            s.rejections += len(issues)
        ws.write(s.critique_rel, json.dumps(
            {"brief_id": f"{s.incident_id}_v{s.version}", "verdict": verdict, "issues": issues,
             "checked_at": now().isoformat()}, indent=2))
        return f"Recorded verdict: {verdict}."

    return {t.name: t for t in (confirm_building, *map(pull_tool, LAYERS), submit_considerations,
                                submit_critique)}


# ---------- agent ----------

def _model(thinking: bool = True):
    from langchain_google_genai import ChatGoogleGenerativeAI

    # "low" is the lowest thinking level this model accepts ("minimal" is a 400) and default
    # thinking roughly doubles every turn. thinking_budget=0 asks for none, but it is a hint, not a
    # switch: in an 8-call A/B on the layer-note turn the model still reasoned in 6 of 8 calls
    # (3 of 8 at "low"). There is no setting that reliably turns thinking off on this model.
    effort = {"thinking_level": "low"} if thinking else {"thinking_budget": 0}
    return ChatGoogleGenerativeAI(model=os.environ["GEMINI_FAST_MODEL"],
                                  google_api_key=os.environ["GEMINI_API_KEY"], **effort)


def _layer_subagents(tools: dict[str, BaseTool], quick: Any) -> list[dict]:
    """The costar / matterport / vexcel subagents: one pull, one short summary. Two model calls each."""
    return [{"name": layer,
             "description": f"Pulls {LAYERS_INFO[layer][0]} for the building and returns a short summary. "
                            "Independent of the other data layers.",
             "system_prompt": LAYER_PROMPT.format(layer=layer, what=LAYERS_INFO[layer][0],
                                                  focus=LAYERS_INFO[layer][1]),
             "tools": [tools[f"pull_{layer}"]], "model": quick} for layer in LAYERS]


def build_prefetcher(s: Session, model: Any = None):
    """A small deep agent that only fans out to the three layer subagents (scripts/prefetch.py)."""
    return create_deep_agent(
        model=model or _model(),
        system_prompt=PREFETCH_PROMPT,
        subagents=_layer_subagents(make_tools(s), model or _model(thinking=False)),
        name="flamescout-prefetch",
    )


def build_orchestrator(s: Session, model: Any = None, checkpointer: Any = None):
    tools = make_tools(s)
    # Mechanical work (one tool call, one short summary) and the critic ask for no thinking: at "low"
    # a turn sometimes spent 1000+ reasoning tokens (13 s).
    quick = model or _model(thinking=False)
    subagents = [
        *_layer_subagents(tools, quick),
        {"name": "strategy",
         "description": "Drafts up to 5 advisory considerations from incident.json and dossier.json and "
                        "writes the brief for the version it is told. Can revise given critic issues.",
         "system_prompt": STRATEGY_PROMPT, "tools": [tools["submit_considerations"]]},
        {"name": "critic",
         "description": "Checks a brief file: advisory wording, evidence support. Returns pass or revise.",
         "system_prompt": CRITIC_PROMPT, "tools": [tools["submit_critique"]], "model": quick},
    ]
    return create_deep_agent(
        model=model or _model(),
        tools=[tools["confirm_building"]],
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware()],  # write_todos is opt-in in deepagents 0.7
        subagents=subagents,
        # Only /workspace/ is persistent (MongoDB or disk); offloaded tool results etc. stay in ephemeral state.
        backend=CompositeBackend(default=StateBackend(), routes={"/workspace/": s.ws.backend()}),
        # First match wins: the model may write notes, nothing else in the workspace.
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/workspace/notes/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="deny"),
        ],
        # The dispatcher picks the building: the answer is an "edit" that fills chosen_building_id.
        interrupt_on={"confirm_building": {"allowed_decisions": ["edit", "reject"]}},
        checkpointer=checkpointer or _checkpointer(),
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


def _save_layer_notes(s: Session, update: dict) -> None:
    """A layer subagent's reply (the result of its task call) becomes notes/<layer>_summary.md."""
    messages = update.get("messages") or []
    messages = getattr(messages, "value", messages)
    for m in messages if isinstance(messages, list) else [messages]:
        for call in getattr(m, "tool_calls", None) or []:
            if call["name"] == "task" and call["args"].get("subagent_type") in LAYERS:
                s.layer_tasks[call["id"]] = call["args"]["subagent_type"]
        layer = s.layer_tasks.pop(getattr(m, "tool_call_id", None), None)
        if layer and (text := m.text.strip()):
            s.ws.write(f"notes/{layer}_summary.md", text + "\n")


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
                if not chunk["ns"]:
                    _save_layer_notes(s, update)
                if not chunk["ns"] and "todos" in update:  # the orchestrator's own plan
                    s.todos = [TodoItem(**t) for t in update["todos"]]
                    _publish(s, "running")
    except Exception as e:  # the crew still gets the facts
        log.exception("Orchestrator %s failed for %s", phase, s.incident_id)
        s.error = f"the orchestrator run failed ({type(e).__name__})"


def _result(s: Session, t0: float, phase: str, brief: Optional[Brief] = None, critique: Optional[dict] = None,
            fallback_reason: Optional[str] = None) -> RunResult:
    _publish(s, "done" if s.status == "briefed" else s.status)
    seconds = time.perf_counter() - t0
    if s.publish:
        _save_state(s)
        detail: dict[str, Any] = {"status": s.status, "version": s.version, "error": s.error,
                                  "trace_run_id": s.trace_run_id}
        if brief is not None:
            detail |= {"considerations": len(brief.considerations), "fallback": fallback_reason is not None,
                       "prefetched": s.prefetched_at is not None}
            db.save_brief(brief, critique, fallback_reason, seconds, s.trace_run_id)
        if phase == "resume" and brief is not None and s.start_seconds:  # unknown after a restart
            detail["time_to_brief_s"] = s.start_seconds + seconds  # agent work only, not the dispatcher's wait
        db.log_event(s.incident_id, phase, seconds, **detail)
    return RunResult(incident_id=s.incident_id, status=s.status, version=s.version, brief=brief,
                     candidates=s.candidates, todos=s.todos, critique=critique,
                     fallback_reason=fallback_reason, seconds=seconds, trace_run_id=s.trace_run_id)


def _finish_brief(s: Session, t0: float, phase: str) -> RunResult:
    """Settle the current version: keep the agent's brief, or fall back to facts only (PRD FR-6)."""
    critique_text = s.ws.read_or_none(s.critique_rel)
    critique = json.loads(critique_text) if critique_text else None
    brief_text = s.ws.read_or_none(s.brief_rel)
    fallback_reason: Optional[str] = None
    if brief_text is None:
        fallback_reason = s.error or "the strategy agent produced no accepted considerations"
    elif critique and critique["verdict"] == "revise":
        fallback_reason = "the critic still flagged issues: " + "; ".join(critique["issues"])

    if fallback_reason:
        _ensure_dossier(s)
        brief = _write_brief(s, [], extra_gaps=(f"Considerations withheld: {fallback_reason}",))
    else:
        brief = Brief.model_validate_json(brief_text)
        if critique is None:
            brief.gaps.insert(0, "Considerations were not checked by the critic")
            s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
    s.status = "briefed"
    return _result(s, t0, phase, brief, critique, fallback_reason)


def _rehydrate(incident_id: str) -> Optional[Session]:
    """After a restart: rebuild the session from Tiger (incidents row + agent thread) and the workspace."""
    row = db.load_incident_state(incident_id)
    if row is None:
        return None
    s = Session(incident_id=incident_id, ws=incident_workspace(incident_id),
                candidates=[BuildingCandidate(**c) for c in row["candidates"]], status=row["status"],
                building_id=row["building_id"], version=row["version"], prefetched_at=row["prefetched_at"])
    try:
        s.agent = build_orchestrator(s)
    except Exception:
        log.exception("Could not rebuild the orchestrator for %s", incident_id)
    log.info("Incident %s: session restored from Tiger (status %s, v%d)", incident_id, s.status, s.version)
    _SESSIONS[incident_id] = s
    return s


def _session(incident_id: str) -> Session:
    if incident_id in _SESSIONS:
        return _SESSIONS[incident_id]
    if s := _rehydrate(incident_id):
        return s
    raise ValueError(f"No session for incident {incident_id}: call start_incident first "
                     "(without TIGER_DATABASE_URL, sessions do not survive a process restart)")


# ---------- public API (see docs/agents-api.md) ----------

def save_incident(record: IncidentRecord, new_segments: Iterable[CallSegment] = (),
                  workspace_root: Optional[Path] = None) -> None:
    """Save fusion's record as the incident's incident.json, and its facts and new segments to Tiger.

    Call after every FusionEngine.ingest (pass the segments just ingested). workspace_root=None
    means MongoDB when configured, else workspace/.
    """
    incident_workspace(record.incident_id, workspace_root).write(INCIDENT, record.model_dump_json(indent=2))
    db.save_segments(record.incident_id, new_segments)
    db.save_facts(record)


def start_incident(incident_id: str, workspace_root: Optional[Path] = None,
                   on_todos: Optional[Callable[[Progress], None]] = None,
                   model: Any = None, checkpointer: Any = None, use_cache: bool = True) -> RunResult:
    """Start the incident's agent. It plans, then pauses until the dispatcher confirms a building.

    workspace_root=None: MongoDB when MONGODB_URI is set, else workspace/. A path means local files.
    use_cache=False ignores prefetched building data for this incident.
    """
    t0 = time.perf_counter()
    ws = incident_workspace(incident_id, workspace_root)
    candidates = find_candidates(_incident(ws))
    if not candidates:
        raise ValueError(f"Incident {incident_id} has no candidate buildings yet: "
                         "callers gave no location that matches a known building")

    ws.clear(keep=(INCIDENT,))  # earlier output for this incident (briefs, layers, notes, ...)
    ws.write(CANDIDATES, json.dumps([c.model_dump() for c in candidates], indent=2))

    saver = checkpointer or _checkpointer()
    saver.delete_thread(incident_id)  # one thread per incident; starting again starts it clean
    db.forget_briefs(incident_id)
    s = _SESSIONS[incident_id] = Session(incident_id=incident_id, ws=ws, candidates=candidates,
                                         on_todos=on_todos, use_cache=use_cache)
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
    s.start_seconds = time.perf_counter() - t0
    return _result(s, t0, "start")


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
        return _result(s, t0, "reject")

    if pending:
        # Built here, not taken from the caller: the tool name and candidate list stay pinned.
        edit = {"type": "edit", "edited_action": {
            "name": "confirm_building",
            "args": {"candidates": pending["args"].get("candidates", []),
                     "chosen_building_id": building_id}}}
        _run(s, Command(resume={"decisions": [edit]}), "resume", t0)
    # else: start_incident never reached the gate; s.error says why and becomes the fallback reason
    s.building_id = building_id
    if _incident(s.ws).confirmed_building_id != building_id:
        _confirm(s.ws, building_id)  # the agent never got there; the dispatcher's choice still stands
    return _finish_brief(s, t0, "resume")


def update_incident(incident_id: str, notes: list[str]) -> RunResult:
    """New information after a brief (fusion change notes). Same thread; writes the next brief version."""
    t0 = time.perf_counter()
    s = _session(incident_id)
    if s.status != "briefed":
        raise ValueError(f"Incident {incident_id} has no brief to update yet (status: {s.status})")
    s.version, s.accepted, s.rejections = s.version + 1, 0, 0
    if _incident(s.ws).confirmed_building_id != s.building_id:
        # Fusion rewrote incident.json from a record that was never told (FusionEngine.confirm).
        log.warning("Incident %s: incident.json lost its confirmed building; restoring %s",
                    incident_id, s.building_id)
        _confirm(s.ws, s.building_id)
    listing = "\n".join(f"- {n}" for n in notes)
    prompt = (f"Update for incident {incident_id}. /workspace/incident.json has changed. "
              f"New information from the calls:\n{listing}\n"
              f"Produce brief version {s.version}: /workspace/brief_v{s.version}.json.")
    _run(s, {"messages": [{"role": "user", "content": prompt}]}, "update", t0)
    return _finish_brief(s, t0, "update")


@dataclass
class PrefetchResult:
    building_id: str
    prefetched_at: datetime
    workspace: Workspace      # the building's prefetch cache (cache.cache_workspace)
    notes: dict[str, str]     # layer -> summary written by its subagent
    seconds: float


def prefetch_building(building_id: str, model: Any = None) -> PrefetchResult:
    """Run the three layer subagents now and cache what they pull (PRD F13).

    An incident confirmed at this building within the next 24 h reuses the cache and skips its
    layer phase. Raises if the building has no data (unknown building id).
    """
    t0 = time.perf_counter()
    folder = cache.cache_workspace(building_id)
    dossier_mod.pull_layer("costar", building_id)  # fail now, before any model call, if the building is unknown
    folder.clear()
    s = Session(incident_id=f"prefetch-{building_id}", ws=folder, building_id=building_id,
                use_cache=False, publish=False)
    try:
        s.agent = build_prefetcher(s, model)
    except Exception:
        log.exception("Could not build the prefetch agent for %s", building_id)
    _run(s, {"messages": [{"role": "user", "content": f"Prefetch building {building_id}."}]}, "prefetch", t0)
    _ensure_dossier(s)  # any layer a subagent did not pull is pulled in code; raises for an unknown building
    at = cache.stamp(building_id)
    hit = cache.load_fresh(building_id)
    seconds = time.perf_counter() - t0
    db.log_event(f"prefetch-{building_id}", "prefetch", seconds, building_id=building_id, error=s.error)
    return PrefetchResult(building_id=building_id, prefetched_at=at, workspace=folder,
                          notes=hit.notes if hit else {}, seconds=seconds)
