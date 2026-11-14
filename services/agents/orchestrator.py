"""Per-incident orchestrator (deepagents).

One persistent deep agent per incident: a checkpointer with thread_id =
incident_id, so the same agent continues from confirmation to brief v1 and on
to v2, v3 ... as new information arrives.

  start_incident   -> plans, then pauses at confirm_building (interrupt_on)
  resume_incident  -> the dispatcher's choice; continues to brief_v1.json
  update_incident  -> new fusion notes; redoes strategy + critic -> brief_vN.json
  review_brief     -> the dispatcher approves or drops low-confidence considerations
                      (publish_brief pauses on them, interrupt_on with a `when` predicate)

The plan depends on the situation and shows in write_todos: no interior scan -> interior
steps skipped, exterior entries pulled from aerial imagery; an unresolved contradiction on
the floor or on trapped occupants -> a copilot question for the dispatcher
(copilot_questions.json).

Subagents and the tools that write the JSON artifacts:

  costar      pull_costar()      -> layers/costar.json
  matterport  pull_matterport()  -> layers/matterport.json
  vexcel      pull_vexcel()      -> layers/vexcel.json
              (delegated in ONE orchestrator turn, so the three run concurrently; each makes two
               model calls: pull, then a short summary that code saves as notes/<layer>_summary.md;
               when the third layer lands, code merges them into dossier.json)
  incident_mapper  get_transcript(), get_plan_index(), submit_overlay()  -> overlay_vN.json
              (the incident overlay for the 3D viewer, docs/INCIDENT_OVERLAY_SPEC.md; runs in
               parallel with the layer subagents, and first on an update)
  strategy    submit_considerations()   -> brief_vN.json  + notes/strategy_reasoning*.md
              (skills loaded on demand from /skills/: occupancy-hazards, size-up-format)
  critic      submit_critique()         -> critique_vN.json (pass | revise, plus flags on
              claims in the layer notes that the layer JSON does not support)

The model plans, delegates, drafts considerations and critiques. The JSON
artifacts are written only by tools (deterministic, schema-checked); the
built-in filesystem tools can read /workspace/ but write only under
/workspace/notes/.

The todo list is exposed three ways: todos.json in the workspace (rewritten on every
change), the on_todos callback, and RunResult.todos.

Long-term memory (memory.py): crew feedback is kept per building and mounted read-only at
/memories/. At a building with memory the strategy subagent reads its crew_notes.md, and code
applies it either way: "Crew reported: ..." facts in the brief, markers marked wrong left out.

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

import contextvars
import json
import re
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from pydantic import BaseModel

from flamescout_schemas import (Brief, BuildingCandidate, BuildingDossier, CallSegment, IncidentOverlay,
                                IncidentRecord,
                                Progress, TodoItem, now)

from . import cache, copilot, db
from . import overlay as overlay_mod
from . import memory as memory_mod
from . import dossier as dossier_mod
from .brief import (ConsiderationDraft, apply_memory, build_brief, check_considerations, low_confidence,
                    merge_evidence, observed_evidence, publish, rejected_count, wrong_markers)
from .dossier import LAYERS, Layer, LayerData
from .locator import find_candidates
from .overlay import OverlayDraft
from .stalls import (MODEL_TIMEOUT_S, RUN_CANCEL, CancelMiddleware, FailoverMiddleware, RunCancelled, StallCounter,
                     StallRetryMiddleware, cancelled)
from .workspace import WORKSPACE_ROOT, Workspace, incident_workspace

log = logging.getLogger(__name__)

WORKSPACE = WORKSPACE_ROOT
INCIDENT, DOSSIER, CANDIDATES, TODOS = "incident.json", "dossier.json", "candidates.json", "todos.json"
MAX_SUBMISSIONS = 3  # per brief version: first draft + 2 regenerations (PRD FR-6)
MAX_OVERLAY_SUBMISSIONS = 3  # per overlay version: first submission + 2 retries
BUDGET_SPENT = ("Stop: the regeneration budget for this brief is used up and the critic did not pass the last "
                "draft. Do not call strategy or critic again and do not publish: the considerations will be "
                "withheld and the facts published. Finish your remaining todos and end your turn.")
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"  # mounted read-only at /skills/
# Wall-clock cap per run (RUN_DEADLINE_S overrides both): past it, outstanding agents are cancelled and
# the best partial brief is published. A hang in front of the crew is worse than a partial brief.
RUN_DEADLINE_V1_S, RUN_DEADLINE_LATER_S = 90.0, 75.0
PREVIOUS_RUN_JOIN_S = 30.0  # a cancelled run's agent finishes its in-flight model call before the next run
# Tools that write the incident's artifacts: after the deadline they are refused, under s.lock. The layer
# pulls are not here (they must run in parallel); _pull checks the deadline itself, under the lock.
WRITER_TOOLS = frozenset({"confirm_building", "write_copilot_question", "publish_brief", "submit_considerations",
                          "submit_critique", "submit_overlay"})
NOTE_HEADER = ("> Unverified agent notes, written by the {layer} subagent. Not checked against the data: "
               "the source of truth is layers/{layer}.json.\n\n")

ORCHESTRATOR_PROMPT = """You are the FlameScout incident orchestrator. Fire calls are coming in and the \
first-arriving crew needs an advisory brief before they arrive. Speed matters: use as few turns as \
possible and batch tool calls exactly as described.

The incident workspace is mounted at /workspace/. You never write files yourself; subagents do that.
Subagents cannot see this conversation, so every task description must be self-contained.

A. FIRST BRIEF. The user message starts with "New incident".
1. First turn, two tool calls together:
   - read_file("/workspace/incident.json", limit=1000)
   - write_todos with eight todos: "Confirm building with dispatcher" (in_progress), "CoStar property
     record", "Matterport interior scan", "Vexcel aerial imagery", "Map reports onto the plan",
     "Draft considerations", "Critique brief", "Brief ready" (all pending)
2. Next turn, one tool call only: confirm_building(candidates=[the candidate building ids from the
   user message, in the same order]). The run pauses until the dispatcher answers. If the dispatcher
   rejects the candidates, reply "No building confirmed." and do nothing else.
3. Next turn, the fan-out: all of these tool calls together, never one at a time (calling them in the
   same turn makes them run in parallel):
   - write_todos (confirm completed; "Map reports onto the plan" and the data todos in_progress)
   - task(subagent_type="incident_mapper"): ask it to map what the callers reported onto the plan,
     for overlay version 1
   - task(subagent_type="costar"), task(subagent_type="matterport"), task(subagent_type="vexcel"), each
     asked to pull its data layer for this incident. If confirm_building answered that the building
     data was PREFETCHED, leave these three out (the data is already in the workspace) and treat the
     three data todos as completed.
4. RE-PLAN when there is NO INTERIOR SCAN (the matterport subagent or confirm_building said so). Only
   then, one turn, two tool calls together:
   - write_todos: the three data todos completed, with "Matterport interior scan" renamed to
     "No interior scan on file: interior steps skipped", and a new todo "Exterior entries from aerial
     imagery" (in_progress) inserted after "Vexcel aerial imagery"
   - task(subagent_type="vexcel"): ask it for the EXTERIOR ENTRIES, because there is no interior scan.
   With an interior scan, skip this step.
5. Next turn, together: write_todos (data todos completed, draft in_progress) and
   task(subagent_type="strategy"): ask it to draft brief version 1, with the incident overlay at
   /workspace/overlay_v1.json. Tell it about any unresolved contradiction between callers that you
   saw in incident.json, and if there is no interior scan.
   LIFE-SAFETY CONTRADICTION: if incident.json has an unresolved contradiction on incident.floor or
   people.trapped, in this same turn also call write_copilot_question(key=<that key>) and include a
   todo "Ask dispatcher: <floor or trapped> contradiction" (completed) in write_todos.
   CREW MEMORY: if confirm_building said crews left notes about this building, include a todo "Read
   crew memory for this building" (completed with the draft) and give the strategy subagent the
   /memories/... path it named, here and in every later strategy task for this incident.
6. Next turn, two tool calls together: write_todos (draft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_v1.json AND the layer notes.
7. Only if the critic's verdict is "revise": task the strategy subagent again, quoting the critic's
   issues and saying which revision number this is, then task the critic again. At most 2 revisions.
8. Last tool turn, two tool calls together: write_todos with everything completed, and
   publish_brief(). If a consideration has low confidence the run pauses there until the dispatcher
   approves or drops it. Then reply with one line: the critic's verdict.

B. UPDATE. The user message starts with "Update": new information arrived after a brief was published,
and the message says which brief version N to produce. The building and its dossier have not changed,
so redo only what the new information affects: never call confirm_building or the costar,
matterport or vexcel subagents.
1. One turn, two tool calls together: write_todos (keep every earlier todo as it is, add "Update
   incident overlay (vN)" in_progress, "Redraft considerations (vN)" and "Critique brief vN" pending)
   and task(subagent_type="incident_mapper"): ask it to update the overlay to version N, quoting the
   new information word for word.
2. Next turn, together: write_todos (overlay completed, redraft in_progress) and
   task(subagent_type="strategy"): ask it to draft brief version N with the incident overlay at
   /workspace/overlay_vN.json, quoting the new information word for word.
   LIFE-SAFETY CONTRADICTION: if the new information has a contradiction (a line starting with "!")
   on incident.floor or people.trapped, in this same turn also call write_copilot_question(key=<that
   key>) and add a todo "Ask dispatcher: <floor or trapped> contradiction" (completed).
3. Next turn, two tool calls together: write_todos (redraft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_vN.json only (not the notes).
4. Only if the verdict is "revise": same revision loop as A7.
5. Last tool turn, two tool calls together: write_todos with everything completed, and
   publish_brief(). Then reply with one line: the critic's verdict.

Apart from the one read_file in A1, never call read_file, ls, glob or grep: do not re-check the
subagents' work yourself. Never state facts about the building or the incident yourself, and never
contact anyone."""

LAYER_PROMPT = """You pull one data layer of a building dossier for fire crews: {what}. \
Use exactly two turns:
1. Call pull_{layer}() once. It saves the layer and returns a digest.
2. Reply with a summary of 2 to 4 short lines: what this layer tells a fire crew ({focus}) and what
   is missing or unverified. Keep the evidence ids in brackets next to each point. Use only what the
   digest says; never guess. Your reply is saved as this layer's note, so write nothing else.
{extra}Do not call any other tool."""

VEXCEL_EXTRA = """If your task asks for EXTERIOR ENTRIES (the building has no interior scan), call \
pull_vexcel_entries() instead of pull_vexcel(), and summarise only the entries it adds, saying they are \
seen from the air and not verified.
"""

PREFETCH_PROMPT = """You prefetch building data before any incident. In ONE turn make three tool calls \
together: task(subagent_type="costar"), task(subagent_type="matterport"), task(subagent_type="vexcel"), each \
asked to pull its data layer for the building in the user message. Then reply "done". Never call them \
one at a time, and call nothing else."""

MAPPER_PROMPT = """You place what callers reported onto the building plan, for the incident overlay the \
crew's 3D viewer draws. Your task says which overlay version N you produce. Use exactly three turns:
1. One turn, together: get_transcript(), get_plan_index(), and for N > 1
   read_file("/workspace/overlay_v<N-1>.json", limit=2000).
2. submit_overlay with everything the callers reported. If it answers "Rejected", fix exactly what it
   lists and submit again (it accepts at most two more tries).
3. Reply with one sentence: what you mapped and what stays unlocated.

What to submit:
- occupants (count, or count_range [min, max] when unsure; "someone" is 1), fire (kind smoke, fire or
  heat), hazards (kind gas, chemical, battery, electrical, structural or other), blocked (ref_id of a
  door, stairs or room reported impassable). Each with: id (short, e.g. occ-1, fire-1, haz-1),
  status "reported" (never "verified"), confidence 0-1, evidence_ids (span ids from get_transcript,
  caller lines only), note (the caller's words, short), and location.
- location: the most precise level the caller's words support, never more:
  - room: the words name a kind of room AND the plan index has exactly one room of that kind on that
    floor (one kitchen) -> {"kind": "room", "room_id": ...}
  - anchor: a room as above, plus the words place it at one of its windows, doors or stairs, or a
    side -> {"kind": "anchor", "anchor": {"room_id", "position", "ref_id"}}; ref_id is an opening id
    for near_door / near_window, a stairs id for near_stairs
  - floor: only the floor is known ("upstairs" in a two-storey building is 2, ground floor is 1), or the
    room is ambiguous (two bedrooms upstairs and nothing in the index tells them apart)
    -> {"kind": "floor", "floor_reported": n}
  - unknown: nothing locates it -> {"kind": "unknown"}
  Never guess a room to look precise. Ids only from get_plan_index; never coordinates.
- If get_plan_index says there is NO INTERIOR SCAN: only floor and unknown locations, and no room,
  opening or stairs ids anywhere.
- Callers disagree (different calls give different locations or counts for the same people or the same
  fire): one entity per caller, each with status "contradicted" and the same group_id, plus one
  contradictions entry {"group_id", "key" (e.g. "occupants.location"), "resolved_by_dispatcher": false}.
  Never pick a winner. One caller correcting themselves is not a contradiction.
- objectives: "rescue" for reported occupants (target kind occupant, occupant_group, room or floor),
  "recon" for hazards and unclear reports; priority 1 first; rationale; evidence_ids. A floor target
  is {"kind": "floor", "floor_reported": n}, with ref_id only if it is a floor id from get_plan_index.
- considerations (optional, at most 3): text starts with "Consider" and is advisory; rationale;
  confidence; evidence_ids; refs = ids in this overlay. No directive words (must, immediately, ordered,
  guaranteed, 100%). Nothing about windows the plan does or does not show.
- Leave out routes, reported_at and version: code adds them.
- For N > 1: keep the id of every entity that is still the same report; new reports get new ids.
Never state anything the callers did not say."""

LAYERS_INFO = {
    "costar": ("the CoStar property record", "use, stories, tenants, tenant-related hazards"),
    "matterport": ("the Matterport interior scan", "entrances, stairs, elevator, key box, FDC, standpipes"),
    "vexcel": ("Vexcel aerial imagery", "approach and rear access, roof, adjacent structures"),
}

STRATEGY_PROMPT = """You draft advisory considerations for the first-arriving fire crew. Your task says \
which brief version N you are drafting. Use exactly three turns:
1. One turn, all tool calls together: read_file("/workspace/incident.json", limit=1000),
   read_file("/workspace/dossier.json", limit=1000), and read_file(<path>, limit=1000) for the SKILL.md
   of every skill in your skills list that applies to this draft, the crew memory file if your
   task names one (a /memories/... path), and the incident overlay your task names
   (/workspace/overlay_v<N>.json). Do not spend an extra turn on skills, memory or the overlay.
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
  layers where both pieces of evidence really support the consideration, and only evidence that backs
  a fact or marker in the dossier counts (imagery coverage with no analysed image does not).
- If the dossier has "scan_available": false there is no interior scan: say nothing about the
  interior. Markers of kind "entry" then come from aerial imagery and are the only entries known:
  use them where they matter, say they are seen from the air and not verified, and cite their
  evidence. They carry low confidence, so the dispatcher reviews them before the brief is published.
- Files under /workspace/notes/ are unverified agent notes. Never take a fact from them.
- Crew memory lists what crews reported at this building at earlier incidents. Say "a crew reported"
  and cite the evidence id given on that line. Never recommend an item a crew marked wrong, and do
  not name it: the checks reject any consideration that does.
- The incident overlay places what callers reported on the building plan. When a consideration is
  about one of its entries, put that id in refs (occupant, fire, hazard or blocked ids, contradiction
  group ids, objective ids): ids from that file only. Its evidence ids (like "c1:12.0-20.5") are
  transcript spans and may be cited too.
- Say nothing about windows the plan does or does not show.
- If the dossier has "contradictions" (the listing and the scan disagree, e.g. on bedrooms), make one
  consideration about the one that matters most for the crew, citing the evidence of both sides,
  and do not pick a side."""

CRITIC_PROMPT = """You are the critic for a fire-crew advisory brief. Use exactly three turns:
1. read_file(<the brief path given in your task>, limit=3000). If your task also asks you to check the
   layer notes, read these in the same turn (limit=1000 each): /workspace/notes/costar_summary.md,
   /workspace/notes/matterport_summary.md, /workspace/notes/vexcel_summary.md and
   /workspace/layers/costar.json, /workspace/layers/matterport.json, /workspace/layers/vexcel.json.
2. Call submit_critique once.
3. Reply with the verdict and, for "revise", the issues, in one or two sentences.

Check every item in "considerations" against the brief's own "size_up", "markers", "hazards",
"exposures", "gaps" and "evidence":
- The wording is advisory ("Consider ..."), never an order.
- Each cited evidence id supports the title and rationale: find the facts or markers that cite it.
  Every cited id already exists in "evidence" (code checks that before the brief is written), so never
  report an id as missing or not present; judge only whether what it says supports the consideration.
- Nothing is stated that the brief does not contain. No invented numbers, locations or tenants.
- Caller reports are described as reported, and a contradiction listed in "gaps" is not silently
  resolved in favour of one caller.
- Nothing relies on a fact whose status is "unverified".
- Where "cross_layer" is true, the pieces of evidence from different sources are about the same place
  or route and fit together physically. Coming from different sources is what cross_layer means: never an
  issue by itself.
- Each issue names the consideration id and says in one sentence what is unsupported or wrong.
verdict "pass" only if every consideration passes. Otherwise "revise", with one specific issue per
problem that names the consideration id (c1, c2, ...). Do not ask for stylistic changes.

Layer notes (only when your task asks): each note was written by a subagent and is unverified. For
every claim ABOUT THE BUILDING in a note that its own layer JSON does not contain or contradicts, add
one note_flag with the layer and the claim quoted from the note. "The building has no other
standpipes" is such a claim when the layer JSON does not say so. A note saying that the layer lacks
some information, or that something is unverified, is not a claim about the building: never flag
that. Note flags never change the verdict."""

REJECT_MESSAGE = ("The dispatcher says none of the candidates is the right building. Stop here: do not "
                  "confirm a building, load a dossier or draft a brief.")

Status = Literal["awaiting_confirmation", "awaiting_approval", "briefed", "rejected"]


class NoteFlag(BaseModel):
    layer: Layer
    claim: str  # quoted from notes/<layer>_summary.md


@dataclass
class RunResult:
    incident_id: str
    status: Status
    version: int                         # brief version this call worked on
    brief: Optional[Brief]               # the published brief, or the draft while awaiting_approval
    candidates: list[BuildingCandidate]
    todos: list[TodoItem]
    critique: Optional[dict]
    fallback_reason: Optional[str]       # set when the brief is facts-only
    seconds: float                       # wall clock of this call
    trace_run_id: Optional[str]          # LangSmith run id of this call, None when tracing is off
    needs_review: list[str] = field(default_factory=list)  # low-confidence consideration ids (awaiting_approval)
    overlay: Optional[IncidentOverlay] = None  # overlay_vN.json of this version (None before a brief)
    overlay_rejections: int = 0          # mapper submissions refused for this version
    overlay_seconds: Optional[float] = None  # seconds into this call when the overlay was written (on_overlay)
    model_stalls: int = 0                # model calls in this call that hit the 12 s timeout
    model_retries: int = 0               # retries made in this call (each stall is retried once)
    partial_reason: Optional[str] = None  # set when the run deadline cut this call short (brief.partial)
    subagents_gave_up: list[str] = field(default_factory=list)  # subagents that failed; the run went on


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
    overlay_attempts: int = 0    # mapper submissions for the current version
    overlay_rejections: int = 0  # of which refused
    agent: Any = None
    error: Optional[str] = None
    todos: list[TodoItem] = field(default_factory=list)
    on_todos: Optional[Callable[[Progress], None]] = None
    on_overlay: Optional[Callable[[IncidentOverlay], None]] = None  # fired as soon as an overlay is written
    leg_t0: float = 0.0                        # perf_counter() at the start of the current public call
    overlay_seconds: Optional[float] = None    # seconds into the current call when its overlay was written
    trace_run_id: Optional[str] = None
    lock: Any = field(default_factory=threading.RLock)  # the three layer tools run concurrently
    use_cache: bool = True                     # reuse prefetched layers (data/cache) when fresh
    prefetched_at: Optional[datetime] = None   # set when the layers came from the cache
    publish: bool = True                       # write todos.json / call on_todos (off for prefetch)
    layer_tasks: dict = field(default_factory=dict)  # task tool_call_id -> layer, to save its reply
    stalls: StallCounter = field(default_factory=StallCounter)  # model timeouts and retries, all agents
    leg_stalls: tuple[int, int] = (0, 0)       # (stalls, retries) of the leg that just ran
    start_seconds: float = 0.0                 # start_incident's wall clock, for time-to-brief
    critic_model: Optional[str] = None         # "provider:model" of the critic, recorded in critique_vN.json
    deadline_reason: Optional[str] = None      # set when the current call's run hit its deadline
    gave_up: list[str] = field(default_factory=list)  # subagents that failed in the current call
    worker: Optional[threading.Thread] = None  # the thread streaming the latest run

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


def _memory(s: Session) -> list[memory_mod.CrewRecord]:
    """What crews reported at this building before (newest verdict per item). Not for prefetch runs."""
    return memory_mod.building_memory(_building_id(s)) if s.publish else []


def _merged(s: Session, building_id: str, layers: Iterable[LayerData]) -> BuildingDossier:
    """The layers merged, minus markers a crew marked wrong. The layer files themselves stay raw."""
    return apply_memory(dossier_mod.merge_layers(building_id, layers), _memory(s))


def _refresh_dossier(s: Session) -> None:
    """Crew feedback may have arrived since dossier.json was written: rebuild it from the layers."""
    building_id = _building_id(s)
    with s.lock:
        layers = [_layer_on_disk(s, name, building_id) for name in LAYERS]
        if not all(layers) or not s.ws.exists(DOSSIER):
            return
        fresh = _merged(s, building_id, layers)
        if [m.id for m in fresh.markers] != [m.id for m in _dossier(s.ws).markers]:  # else leave it untouched
            s.ws.write(DOSSIER, fresh.model_dump_json(indent=2))


def _pull(s: Session, layer: str, **options) -> str:
    """Tool body for pull_<layer>: save the layer, merge into dossier.json once all three are in.
    options: exterior_entries=True for the vexcel layer when there is no interior scan."""
    building_id = _building_id(s)
    if not building_id:
        return "Error: the dispatcher has not confirmed a building yet."
    data = _layer_on_disk(s, layer, building_id)
    if data and (data.exterior_entries or not options.get("exterior_entries")):
        return "Already pulled, nothing rewritten.\n" + dossier_mod.digest(data)
    try:
        data = dossier_mod.pull_layer(layer, building_id, **options)
    except (OSError, ValueError, KeyError) as e:
        return f"Error: {e}"
    with s.lock:
        if cancelled():  # the run deadline passed while this layer was being pulled
            return "Error: the run deadline passed; nothing written."
        s.ws.write(s.layer_rel(layer), data.model_dump_json(indent=2))
        layers = [_layer_on_disk(s, name, building_id) for name in LAYERS]
        if all(layers):
            s.ws.write(DOSSIER, _merged(s, building_id, layers).model_dump_json(indent=2))
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
        dossier = _merged(s, building_id, hit.layers.values())
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
        s.ws.write(DOSSIER, _merged(s, building_id, layers).model_dump_json(indent=2))


def _ensure_exterior_entries(s: Session) -> None:
    """No interior scan: entries come from aerial imagery. Pull them in code if the agent did not re-plan."""
    building_id = _building_id(s)
    with s.lock:
        if _dossier(s.ws).scan_available:
            return
        vexcel = _layer_on_disk(s, "vexcel", building_id)
        if vexcel and vexcel.exterior_entries:
            return
        log.warning("Incident %s: no interior scan and the exterior entries were not pulled; pulling in code",
                    s.incident_id)
        s.ws.write(s.layer_rel("vexcel"),
                   dossier_mod.pull_layer("vexcel", building_id, exterior_entries=True).model_dump_json(indent=2))
        layers = [_layer_on_disk(s, name, building_id) for name in LAYERS]
        s.ws.write(DOSSIER, _merged(s, building_id, layers).model_dump_json(indent=2))


def _overlay_context(s: Session) -> tuple[dict, set[str]]:
    """This version's overlay, as the brief needs it: the transcript spans it cites, and its ids."""
    ov = overlay_mod.read(s.ws, s.version)
    return overlay_mod.span_evidence(overlay_mod.cited_spans(ov, overlay_mod.transcript(s.ws))), \
        overlay_mod.overlay_ids(ov)


def _model_id(s: Session) -> str:
    """The overlay's model_id: the building's Matterport model id, else its building id."""
    building_id = _building_id(s)
    try:
        model = dossier_mod.get_providers().matterport.find_model({"property_id": building_id})
    except Exception:  # noqa: BLE001 - only a label for the viewer
        model = None
    return (model or {}).get("model_id") or building_id or s.incident_id


def _write_overlay(s: Session, ov: IncidentOverlay) -> None:
    """Write overlay_vN.json and hand it on at once (on_overlay): the viewer gets it before the brief."""
    s.ws.write(overlay_mod.overlay_rel(s.version), overlay_mod.dump(ov))
    s.overlay_seconds = time.perf_counter() - s.leg_t0 if s.leg_t0 else None
    if s.on_overlay and s.publish:
        try:
            s.on_overlay(ov)
        except Exception:  # noqa: BLE001 - a failing listener must not stop the brief
            log.exception("on_overlay failed for %s v%d", s.incident_id, s.version)


def _ensure_overlay(s: Session) -> None:
    """Every brief version has an overlay of the same version: the fallback when the mapper gave none."""
    if s.ws.exists(overlay_mod.overlay_rel(s.version)):
        return
    reason = (f"the incident mapper's {s.overlay_rejections} submission(s) were all rejected"
              if s.overlay_rejections else s.error or "the incident mapper did not submit one")
    log.warning("Incident %s: no overlay v%d (%s); writing the empty fallback", s.incident_id, s.version, reason)
    _write_overlay(s, overlay_mod.fallback(s.incident_id, _model_id(s), s.version, reason))


def _write_brief(s: Session, drafts: list[ConsiderationDraft], extra_gaps: tuple[str, ...] = (),
                 published: bool = False) -> Brief:
    prev_text = s.ws.read_or_none(f"brief_v{s.version - 1}.json")
    previous = Brief.model_validate_json(prev_text) if prev_text else None
    spans, ids = _overlay_context(s)
    brief = build_brief(_incident(s.ws), _dossier(s.ws), drafts, version=s.version, extra_gaps=extra_gaps,
                        previous=previous, verifier_rejections=s.rejections, prefetched_at=s.prefetched_at,
                        memory=_memory(s), extra_evidence=spans, overlay_ids=ids)
    brief.published = published
    s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
    return brief


def _brief(s: Session) -> Optional[Brief]:
    text = s.ws.read_or_none(s.brief_rel)
    return Brief.model_validate_json(text) if text else None


def _critique(s: Session) -> Optional[dict]:
    text = s.ws.read_or_none(s.critique_rel)
    return json.loads(text) if text else None


def _needs_review(s: Session) -> list[str]:
    """Ids of the low-confidence considerations the dispatcher still has to approve or drop."""
    brief, critique = _brief(s), _critique(s)
    if brief is None or brief.published or not critique or critique["verdict"] != "pass":
        return []
    return [c.id for c in low_confidence(brief)]


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
        remembered = _memory(s)
        crew = (f" CREW MEMORY: crews left {len(remembered)} note(s) about this building at earlier incidents, "
                f"in {memory_mod.notes_path(chosen_building_id)}. Give that path to the strategy subagent."
                if remembered else "")
        if _load_prefetched(s):
            scan = "" if _dossier(ws).scan_available else " There is NO INTERIOR SCAN on file for it."
            return (f"The dispatcher confirmed building {chosen_building_id}. Its building data was "
                    "PREFETCHED and is already in /workspace/dossier.json: skip the costar and matterport "
                    f"subagents and the first vexcel pull.{scan}{crew}")
        return (f"The dispatcher confirmed building {chosen_building_id}. No prefetched data: pull the "
                f"three layers.{crew}")

    @tool(parse_docstring=True)
    def write_copilot_question(key: Literal["incident.floor", "people.trapped"]) -> str:
        """Ask the dispatcher to resolve a contradiction between callers. Writes copilot_questions.json.

        Args:
            key: The contradicted fact: "incident.floor" or "people.trapped".
        """
        written = copilot.write_questions(ws, _incident(ws), keys=[key])
        if not written:
            return f"Error: incident.json has no unresolved contradiction on {key}."
        return f"Dispatcher asked: {written[0].question}"

    @tool(parse_docstring=True)
    def publish_brief(drop: list[str] = []) -> str:  # noqa: B006 - a tool schema default, never mutated
        """Publish the current brief version to the crew. Pauses for the dispatcher when a consideration has low confidence.

        Args:
            drop: Leave empty. The dispatcher lists consideration ids to drop here.
        """
        brief, critique = _brief(s), _critique(s)
        if brief is None:
            return f"Error: /workspace/{s.brief_rel} does not exist yet."
        if brief.published:
            return f"/workspace/{s.brief_rel} is already published."
        if _budget_spent(s):
            return BUDGET_SPENT
        if not critique or critique["verdict"] != "pass":
            return "Error: the critic has not passed this brief. Do not publish it."
        try:
            brief = publish(brief, drop)
        except ValueError as e:
            return f"Error: {e}"
        ws.write(s.brief_rel, brief.model_dump_json(indent=2))
        dropped = f" Dropped by the dispatcher: {', '.join(drop)}." if drop else ""
        return f"Published /workspace/{s.brief_rel} with {len(brief.considerations)} considerations.{dropped}"

    def pull_tool(layer: str) -> BaseTool:
        def pull() -> str:
            return _pull(s, layer)

        return StructuredTool.from_function(
            pull, name=f"pull_{layer}",
            description=f"Pull {LAYERS_INFO[layer][0]} for the building and save it as "
                        f"layers/{layer}.json. Returns a digest with the evidence ids.")

    @tool
    def pull_vexcel_entries() -> str:
        """Pull Vexcel aerial imagery again, this time including building entries read from the oblique views.

        Only for buildings with no interior scan. The entries are low confidence and not verified.
        Rewrites layers/vexcel.json and returns a digest with the evidence ids.
        """
        return _pull(s, "vexcel", exterior_entries=True)

    @tool(parse_docstring=True)
    def submit_considerations(considerations: list[ConsiderationDraft]) -> str:
        """Submit the ranked considerations. If they pass the checks, the brief for this version is written.

        Args:
            considerations: At most 5, most important first. Each title starts with "Consider ".
        """
        if s.accepted >= MAX_SUBMISSIONS:
            return "Error: regeneration budget used up. Do not resubmit. " + BUDGET_SPENT
        if not _building_id(s):
            return "Error: the dispatcher has not confirmed a building yet."
        _ensure_dossier(s)
        _ensure_exterior_entries(s)
        _refresh_dossier(s)  # crew feedback given since the dossier was written
        remembered = _memory(s)
        spans, ids = _overlay_context(s)
        evidence = {**spans, **merge_evidence(_incident(ws), _dossier(ws), remembered)}
        wrong = wrong_markers(remembered)
        observed = observed_evidence(apply_memory(_dossier(ws), remembered))
        if errors := check_considerations(considerations, evidence, wrong, ids, observed):
            s.rejections += rejected_count(considerations, evidence, wrong, ids, observed)
            return "Rejected, nothing written. Fix these and resubmit:\n- " + "\n- ".join(errors)
        _write_brief(s, considerations)
        ws.delete(s.critique_rel)  # any earlier critique is about an older draft
        s.accepted += 1
        return (f"Accepted: /workspace/{s.brief_rel} written with "
                f"{len(considerations)} considerations.")

    @tool(parse_docstring=True)  # not return_direct: task() would hand the orchestrator an empty result
    def submit_critique(verdict: Literal["pass", "revise"], issues: list[str],
                        note_flags: list[NoteFlag] = []) -> str:  # noqa: B006 - schema default, never mutated
        """Record the verdict on the current brief version, and any unsupported claims in the layer notes.

        Args:
            verdict: "pass" if every consideration is advisory and supported, otherwise "revise".
            issues: One specific issue per problem, naming the consideration id. Empty for "pass".
            note_flags: Claims in a layer note that its layer JSON does not support. Empty if none or not asked.
        """
        if not ws.exists(s.brief_rel):
            return f"Error: /workspace/{s.brief_rel} does not exist yet."
        if verdict == "revise" and not issues:
            return 'Error: "revise" needs at least one issue.'
        if verdict == "revise" and (vague := [i for i in issues if not _specific_issue(i)]):
            return (f"Error: nothing recorded. Each issue must name the consideration id and say what is wrong "
                    f"in a sentence; these do not: {vague}. Call submit_critique again.")
        if verdict == "revise" and (wrong := _missing_evidence_claims(issues, ws.read(s.brief_rel))):
            return ("Error: nothing recorded. These evidence ids are in the brief's \"evidence\" (code checks "
                    "every cited id exists): " + "; ".join(wrong) + ". Judge only whether each one supports "
                    "its consideration, then call submit_critique again.")
        if verdict == "revise":
            s.rejections += len(issues)
        ws.write(s.critique_rel, json.dumps(
            {"brief_id": f"{s.incident_id}_v{s.version}", "verdict": verdict, "issues": issues,
             "note_flags": [f.model_dump() for f in note_flags], "critic_model": s.critic_model,
             "checked_at": now().isoformat()}, indent=2))
        for flag in note_flags:  # mark the note itself, so nobody reads the claim without the flag
            rel = f"notes/{flag.layer}_summary.md"
            if (note := ws.read_or_none(rel)) is not None:
                ws.write(rel, note.rstrip("\n") + f"\n\n> Critic flag, not supported by layers/{flag.layer}.json: "
                                                 f"{flag.claim}\n")
        return f"Recorded verdict: {verdict}." + (f" {len(note_flags)} note claim(s) flagged." if note_flags else "")

    @tool
    def get_transcript() -> str:
        """The calls so far, one line per segment, each with the span id to cite as evidence (caller lines only)."""
        spans = overlay_mod.transcript(ws)
        if not spans:
            return "No transcript yet."
        return "\n".join(f"{sp.span_id} [{sp.speaker}] {sp.text}" for sp in spans)

    @tool
    def get_plan_index() -> str:
        """The confirmed building's plan index: floors (numbered from the ground floor, 1), rooms, openings, stairs."""
        index = overlay_mod.load_plan_index(_building_id(s))
        if not overlay_mod.has_scan(index):
            return ("NO INTERIOR SCAN on file for this building: use only floor (floor_reported) and unknown "
                    "locations, and no room, opening or stairs ids.")
        floors = [{"number": n, "floor_id": f} for n, f in enumerate(index["floors"], 1)]
        return json.dumps({"floors": floors, "rooms": index["rooms"], "openings": index["openings"],
                           "stairs": index["stairs"]}, indent=1)

    def overlay_refused(errors: list[str]) -> str:
        s.overlay_rejections += 1
        left = MAX_OVERLAY_SUBMISSIONS - s.overlay_attempts
        return (f"Rejected, nothing written ({left} tr{'y' if left == 1 else 'ies'} left). Fix these and "
                "resubmit:\n- " + "\n- ".join(errors))

    @tool(parse_docstring=True)
    def submit_overlay(overlay: OverlayDraft) -> str:
        """Submit the incident overlay for this version. If it passes the checks, /workspace/overlay_vN.json is written.

        Args:
            overlay: Occupants, fire, hazards, blocked, contradictions, objectives and considerations.
        """
        if s.overlay_attempts >= MAX_OVERLAY_SUBMISSIONS:
            return "Error: submission budget used up. Do not resubmit."
        s.overlay_attempts += 1
        index, spans = overlay_mod.load_plan_index(_building_id(s)), overlay_mod.transcript(ws)
        try:
            ov = overlay_mod.assemble(overlay, s.incident_id, _model_id(s), s.version, index, spans,
                                      previous=overlay_mod.read(ws, s.version - 1))
        except ValueError as e:
            return overlay_refused([str(e)])
        if errors := overlay_mod.check(ov, index, spans):
            return overlay_refused(errors)
        _write_overlay(s, ov)
        return (f"Accepted: /workspace/{overlay_mod.overlay_rel(s.version)} written with {len(ov.occupants)} "
                f"occupant, {len(ov.fire)} fire, {len(ov.hazards)} hazard and {len(ov.blocked)} blocked entries.")

    def schema_refused(error: Exception) -> str:  # the draft did not even parse
        if s.overlay_attempts >= MAX_OVERLAY_SUBMISSIONS:
            return "Error: submission budget used up. Do not resubmit."
        s.overlay_attempts += 1
        return overlay_refused([str(error)[:1500]])

    submit_overlay.handle_validation_error = schema_refused

    return {t.name: t for t in (confirm_building, write_copilot_question, publish_brief,
                                *map(pull_tool, LAYERS), pull_vexcel_entries, submit_considerations,
                                submit_critique, get_transcript, get_plan_index, submit_overlay)}


def _budget_spent(s: Session) -> bool:
    """All drafts for this version are used and the critic sent the last one back: nothing more to do."""
    critique = _critique(s)
    return s.accepted >= MAX_SUBMISSIONS and bool(critique) and critique["verdict"] == "revise"


def _specific_issue(issue: str) -> bool:
    """Names a consideration (c1, c2, ...) and says something about it, not just the id."""
    return bool(re.search(r"\bc\d+\b", issue)) and len(re.sub(r"\bc\d+\b|[\W_]", "", issue)) >= 8


class BudgetStopMiddleware(AgentMiddleware):
    """Once the budget is spent (_budget_spent), the orchestrator's calls to the strategy or critic
    subagent are answered with BUDGET_SPENT instead of run. Without this the agent kept delegating
    redrafts that could only be refused until it hit the recursion limit."""

    def __init__(self, s: Session):
        super().__init__()
        self.s = s

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if (call.get("name") == "task" and (call.get("args") or {}).get("subagent_type") in ("strategy", "critic")
                and _budget_spent(self.s)):
            log.warning("Budget spent for %s v%d: refused task(%s)", self.s.incident_id, self.s.version,
                        call["args"]["subagent_type"])
            return ToolMessage(content=BUDGET_SPENT, tool_call_id=call["id"], name="task")
        return handler(request)


class RunGuardMiddleware(CancelMiddleware):
    """CancelMiddleware, and a writer tool checks the deadline under s.lock: once _finish_brief holds the
    lock after a deadline, a cancelled run can no longer write the brief, critique, overlay or layers."""

    def __init__(self, agent: str, s: "Session"):
        super().__init__(agent)
        self.s = s

    def wrap_tool_call(self, request, handler):
        if request.tool_call.get("name") not in WRITER_TOOLS:
            return super().wrap_tool_call(request, handler)
        with self.s.lock:
            return super().wrap_tool_call(request, handler)


class SubagentGiveUpMiddleware(AgentMiddleware):
    """A subagent that fails for good (its model stalled through the retry and the failover, or raised)
    ends only itself: the orchestrator gets a tool result saying so and carries on. Code fills what
    the subagent did not do (_ensure_dossier, _ensure_overlay, _finish_brief). Interrupts and the run
    deadline still stop the run."""

    def __init__(self, s: "Session"):
        super().__init__()
        self.s = s

    def wrap_tool_call(self, request, handler):
        call = request.tool_call
        if call.get("name") != "task":
            return handler(request)
        try:
            return handler(request)
        except (GraphBubbleUp, RunCancelled):
            raise
        except Exception as e:  # noqa: BLE001 - one subagent's failure must not end the run
            if cancelled():
                raise RunCancelled("run deadline reached") from e
            who = (call.get("args") or {}).get("subagent_type") or "unknown"
            self.s.gave_up.append(who)
            log.error("Subagent %s gave up for %s v%d (%s: %s); the run goes on without it",
                      who, self.s.incident_id, self.s.version, type(e).__name__, str(e)[:120])
            return ToolMessage(
                content=(f"Error: the {who} subagent gave up ({type(e).__name__}). Do not call it again for this "
                         "brief version. Continue with the rest of your plan; code fills in what it did not do."),
                tool_call_id=call["id"], name="task", status="error")


_MISSING = re.compile(r"\b(not (present|found|listed|included|contained)|missing|absent|"
                      r"does(n't| not) exist|not in (the )?(brief|evidence|size_up|markers))", re.I)


def _missing_evidence_claims(issues: list[str], brief_json: str) -> list[str]:
    """Critic issues that call an evidence id missing although the brief's evidence has it, as
    "<id> (<source> <kind>)". Existence is checked in code; the critic judges support only."""
    evidence = json.loads(brief_json).get("evidence", {})
    wrong: list[str] = []
    for issue in issues:
        if not _MISSING.search(issue):
            continue
        for eid, ev in evidence.items():
            if re.search(rf"\b{re.escape(eid)}\b", issue) and eid not in {w.split(" ")[0] for w in wrong}:
                ev = ev if isinstance(ev, dict) else {}
                wrong.append(f"{eid} ({ev.get('source', '?')} {ev.get('kind', 'evidence')})")
    return wrong


# ---------- agent ----------

def _model(thinking: bool = True, prov: Optional[str] = None):
    from .llm import make_chat

    # "low" is the lowest thinking level this model accepts ("minimal" is a 400) and default
    # thinking roughly doubles every turn. thinking_budget=0 asks for none, but it is a hint, not a
    # switch: in an 8-call A/B on the layer-note turn the model still reasoned in 6 of 8 calls
    # (3 of 8 at "low"). There is no setting that reliably turns thinking off on this model.
    # (Gemini only; LLM_PROVIDER=openai uses OPENAI_REASONING_EFFORT, see llm.py.)
    # A call that has not answered in MODEL_TIMEOUT_S is cut off; max_retries=1 means one attempt in
    # the SDK, so the only retry is StallRetryMiddleware's, which logs it with the agent's name.
    return make_chat("fast", timeout=MODEL_TIMEOUT_S, max_retries=1, thinking="low" if thinking else "none",
                     prov=prov)


class _Models:
    """The models one agent graph uses, and its middleware: StallRetry, plus failover when a second
    provider is set (LLM_FALLBACK_PROVIDER). The critic runs on CRITIC_PROVIDER when that differs from
    LLM_PROVIDER, so a different model checks the brief, with LLM_PROVIDER's model as its backup.
    A model passed in (tests, scripts) is used everywhere, with no failover and no separate critic."""

    def __init__(self, stalls: StallCounter, model: Any = None, session: Optional["Session"] = None):
        self.stalls, self.session = stalls, session
        if model is not None:
            self.main = self.quick = self.critic = model
            self.main_backup = self.quick_backup = self.critic_backup = None
            self.critic_provider = None
            return
        from .llm import critic_provider, fallback_provider, provider

        backup = fallback_provider("fast")
        self.main, self.quick = _model(), _model(thinking=False)
        self.main_backup = _model(prov=backup) if backup else None
        self.quick_backup = _model(thinking=False, prov=backup) if backup else None
        self.critic_provider = critic_provider()
        if self.critic_provider != provider():
            self.critic, self.critic_backup = _model(thinking=False, prov=self.critic_provider), self.quick
        else:
            self.critic, self.critic_backup = self.quick, self.quick_backup

    def middleware(self, agent: str, backup: Any) -> list:
        guard = RunGuardMiddleware(agent, self.session) if self.session else CancelMiddleware(agent)
        mw: list = [guard, StallRetryMiddleware(agent, self.stalls)]  # guard outermost: no call after the deadline
        if backup is not None:
            mw.append(FailoverMiddleware(agent, backup, self.stalls))  # inside the retry: fail over first
        return mw


def _layer_subagents(tools: dict[str, BaseTool], m: _Models) -> list[dict]:
    """The costar / matterport / vexcel subagents: one pull, one short summary. Two model calls each."""
    return [{"name": layer,
             "middleware": m.middleware(layer, m.quick_backup),
             "description": f"Pulls {LAYERS_INFO[layer][0]} for the building and returns a short summary. "
                            "Independent of the other data layers.",
             "system_prompt": LAYER_PROMPT.format(layer=layer, what=LAYERS_INFO[layer][0],
                                                  focus=LAYERS_INFO[layer][1],
                                                  extra=VEXCEL_EXTRA if layer == "vexcel" else ""),
             "tools": [tools[f"pull_{layer}"], *([tools["pull_vexcel_entries"]] if layer == "vexcel" else [])],
             "model": m.quick} for layer in LAYERS]


def build_prefetcher(s: Session, model: Any = None):
    """A small deep agent that only fans out to the three layer subagents (scripts/prefetch.py)."""
    m = _Models(s.stalls, model, s)
    return create_deep_agent(
        model=m.main,
        system_prompt=PREFETCH_PROMPT,
        middleware=[*m.middleware("prefetch", m.main_backup), SubagentGiveUpMiddleware(s)],
        subagents=_layer_subagents(make_tools(s), m),
        name="flamescout-prefetch",
    )


def build_orchestrator(s: Session, model: Any = None, checkpointer: Any = None):
    tools = make_tools(s)
    # Mechanical work (one tool call, one short summary) and the critic ask for no thinking: at "low"
    # a turn sometimes spent 1000+ reasoning tokens (13 s).
    m = _Models(s.stalls, model, s)
    if m.critic_provider:
        from .llm import model_name, provider

        s.critic_model = f"{m.critic_provider}:{model_name('fast', m.critic_provider)}"
        if m.critic_provider != provider():
            log.info("Critic runs on %s; strategy drafts on %s", s.critic_model, provider())
    subagents = [
        *_layer_subagents(tools, m),
        {"name": "strategy",
         "middleware": m.middleware("strategy", m.main_backup),
         "description": "Drafts up to 5 advisory considerations from incident.json and dossier.json and "
                        "writes the brief for the version it is told. Can revise given critic issues.",
         "system_prompt": STRATEGY_PROMPT, "tools": [tools["submit_considerations"]],
         # Progressive disclosure: it sees each skill's name and description, and reads the SKILL.md
         # it needs (occupancy-hazards, size-up-format) with read_file.
         "skills": ["/skills/"]},
        {"name": "incident_mapper",
         "middleware": m.middleware("incident_mapper", m.main_backup),
         "description": "Places what callers reported (occupants, fire, hazards, blocked routes) on the building "
                        "plan by ids, with objectives, and writes the incident overlay for the version it is told.",
         "system_prompt": MAPPER_PROMPT,
         "tools": [tools["get_transcript"], tools["get_plan_index"], tools["submit_overlay"]]},
        {"name": "critic",
         "middleware": m.middleware("critic", m.critic_backup),
         "description": "Checks a brief file (advisory wording, evidence support; returns pass or revise) "
                        "and, when asked, the layer notes against the layer JSON.",
         "system_prompt": CRITIC_PROMPT, "tools": [tools["submit_critique"]], "model": m.critic},
    ]
    return create_deep_agent(
        model=m.main,
        tools=[tools["confirm_building"], tools["write_copilot_question"], tools["publish_brief"]],
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware(),  # write_todos is opt-in in deepagents 0.7
                    *m.middleware("orchestrator", m.main_backup), BudgetStopMiddleware(s),
                    SubagentGiveUpMiddleware(s)],
        subagents=subagents,
        # Only /workspace/ is persistent (MongoDB or disk); offloaded tool results etc. stay in ephemeral state.
        backend=CompositeBackend(default=StateBackend(), routes={
            "/workspace/": s.ws.backend(),
            "/skills/": FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True),  # repo skills, read-only
            # Long-term memory: crew feedback per building (/memories/<building_id>/), MongoDB or data/memories/.
            "/memories/": memory_mod.memory_workspace().backend(),
        }),
        # First match wins: the model may write notes, nothing else in the workspace, skills or memory.
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/workspace/notes/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/workspace/**", "/skills/**", "/memories/**"],
                                 mode="deny"),
        ],
        interrupt_on={
            # The dispatcher picks the building: the answer is an "edit" that fills chosen_building_id.
            "confirm_building": {"allowed_decisions": ["edit", "reject"]},
            # Low-confidence considerations wait for the dispatcher: approve as is, or "edit" with ids to drop.
            "publish_brief": {"allowed_decisions": ["approve", "edit"],
                              "when": lambda _request: bool(_needs_review(s))},
        },
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
        if getattr(m, "status", None) == "error":  # the subagent gave up: no note, just the error
            continue
        if layer and (text := m.text.strip()):
            rel = f"notes/{layer}_summary.md"
            earlier = s.ws.read_or_none(rel)  # a second reply (exterior entries) is appended
            start = earlier.rstrip("\n") + "\n\n" if earlier else NOTE_HEADER.format(layer=layer)
            s.ws.write(rel, start + text + "\n")


def _pending(s: Session, tool_name: str) -> Optional[dict]:
    """The gated tool call waiting for the dispatcher, if the run is paused on one."""
    if s.agent is None:
        return None
    for interrupt in s.agent.get_state(s.config).interrupts:
        for action in interrupt.value.get("action_requests", []):
            if action["name"] == tool_name:
                return action
    return None


def _pending_confirmation(s: Session) -> Optional[dict]:
    return _pending(s, "confirm_building")


def run_deadline_s(version: int) -> float:
    """Wall-clock cap of one run: RUN_DEADLINE_S when set, else 90 s for brief v1 and 75 s for later versions."""
    raw = os.getenv("RUN_DEADLINE_S", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.warning("RUN_DEADLINE_S=%r is not a number; using the defaults", raw)
    return RUN_DEADLINE_V1_S if version <= 1 else RUN_DEADLINE_LATER_S


def _run(s: Session, agent_input: Any, phase: str, t0: float) -> None:
    """Stream one leg of the incident's thread, never past its deadline. Never raises: failures land in
    s.error. The stream runs in a worker thread; when the deadline (counted from t0) passes, the run's
    cancel event is set, every agent stops at its next model or tool call, and this returns at once
    with s.deadline_reason set. _finish_brief then publishes the best partial brief."""
    run_id = uuid.uuid4()
    s.trace_run_id = str(run_id) if _enable_tracing() else None
    s.error, s.deadline_reason, s.gave_up = None, None, []
    s.leg_t0 = t0
    stalls_before, retries_before = s.stalls.snapshot()
    if s.worker is not None and s.worker.is_alive():  # a cancelled run still finishing its last model call
        log.warning("Incident %s: waiting for the previous run to stop before %s", s.incident_id, phase)
        s.worker.join(PREVIOUS_RUN_JOIN_S)
    _publish(s, "running")
    cancel, done = threading.Event(), threading.Event()

    def stream() -> None:
        RUN_CANCEL.set(cancel)  # inherited by the tool threads that run the subagents
        try:
            if s.agent is None:
                raise RuntimeError("the orchestrator agent could not be built")
            config = {**s.config, "run_id": run_id, "run_name": f"incident:{s.incident_id}:{phase}",
                      "recursion_limit": 60, "tags": ["flamescout", "orchestrator", phase],
                      "metadata": {"incident_id": s.incident_id, "thread_id": s.incident_id,
                                   "brief_version": s.version}}
            for chunk in s.agent.stream(agent_input, config, stream_mode="updates", subgraphs=True, version="v2"):
                if cancel.is_set():
                    break
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
        except RunCancelled:
            log.info("Incident %s: %s run stopped at its deadline", s.incident_id, phase)
        except Exception as e:  # the crew still gets the facts
            if not cancel.is_set():
                log.exception("Orchestrator %s failed for %s", phase, s.incident_id)
                s.error = f"the orchestrator run failed ({type(e).__name__})"
        finally:
            done.set()

    s.worker = threading.Thread(target=contextvars.copy_context().run, args=(stream,), daemon=True,
                                name=f"orchestrator-{s.incident_id}-{phase}")
    s.worker.start()
    deadline = run_deadline_s(s.version)
    if not done.wait(max(0.0, deadline - (time.perf_counter() - t0))):
        cancel.set()
        with s.lock:  # a writer tool already under way finishes first; none starts after this
            s.deadline_reason = f"run deadline of {deadline:.0f} s reached during {phase}; unfinished agents cancelled"
            s.error = s.deadline_reason
        log.error("Incident %s v%d: %s", s.incident_id, s.version, s.deadline_reason)
    stalls_after, retries_after = s.stalls.snapshot()
    s.leg_stalls = (stalls_after - stalls_before, retries_after - retries_before)


def _result(s: Session, t0: float, phase: str, brief: Optional[Brief] = None, critique: Optional[dict] = None,
            fallback_reason: Optional[str] = None) -> RunResult:
    _publish(s, "done" if s.status == "briefed" else s.status)
    seconds = time.perf_counter() - t0
    published = brief is not None and brief.published
    if s.publish:
        _save_state(s)
        detail: dict[str, Any] = {"status": s.status, "version": s.version, "error": s.error,
                                  "trace_run_id": s.trace_run_id, "model_stalls": s.leg_stalls[0],
                                  "model_retries": s.leg_stalls[1], "deadline_hit": s.deadline_reason is not None,
                                  "subagents_gave_up": list(s.gave_up)}
        if brief is not None:
            detail |= {"considerations": len(brief.considerations), "fallback": fallback_reason is not None,
                       "prefetched": s.prefetched_at is not None, "published": published}
        if published:  # Tiger holds published briefs only; a draft awaiting approval is not one yet
            db.save_brief(brief, critique, fallback_reason, seconds, s.trace_run_id)
        if s.version == 1 and s.start_seconds:  # unknown after a restart
            if published and phase in ("resume", "review"):
                detail["time_to_brief_s"] = s.start_seconds + seconds  # agent work only, not the dispatcher's wait
            elif phase == "resume":
                s.start_seconds += seconds  # awaiting approval: the review leg completes the count
        db.log_event(s.incident_id, phase, seconds, **detail)
    needs_review = [c.id for c in low_confidence(brief)] if brief and s.status == "awaiting_approval" else []
    (stalls, retries), s.leg_stalls = s.leg_stalls, (0, 0)  # a later call that runs no agent reports none
    overlay_seconds, s.overlay_seconds = s.overlay_seconds, None
    return RunResult(incident_id=s.incident_id, status=s.status, version=s.version, brief=brief,
                     candidates=s.candidates, todos=s.todos, critique=critique,
                     fallback_reason=fallback_reason, seconds=seconds, trace_run_id=s.trace_run_id,
                     needs_review=needs_review, model_stalls=stalls, model_retries=retries,
                     overlay=overlay_mod.read(s.ws, s.version) if brief is not None else None,
                     overlay_rejections=s.overlay_rejections, overlay_seconds=overlay_seconds,
                     partial_reason=brief.partial_reason if brief is not None else None,
                     subagents_gave_up=list(s.gave_up))


def _finish_brief(s: Session, t0: float, phase: str) -> RunResult:
    """Settle the current version: publish the agent's brief, hold it for the dispatcher when a
    consideration has low confidence, or fall back to facts only (PRD FR-6).

    After a run deadline: publish the best partial brief at once (facts plus the accepted
    considerations; low-confidence ones are dropped, nobody reviewed them), marked partial."""
    with s.lock:  # a cancelled run's writer tools wait or are refused (RunGuardMiddleware)
        return _settle(s, t0, phase)


def _drop_unreviewed(brief: Brief, reason: str) -> Brief:
    low = [c.id for c in low_confidence(brief)]
    if not low:
        return brief
    out = brief.model_copy(deep=True)
    out.considerations = [c for c in out.considerations if c.id not in low]
    for rank, c in enumerate(out.considerations, 1):
        c.rank = rank
    out.gaps.insert(0, f"Low-confidence consideration(s) {', '.join(low)} left out, not reviewed: {reason}")
    return out


def _settle(s: Session, t0: float, phase: str) -> RunResult:
    critique, brief = _critique(s), _brief(s)
    partial = s.deadline_reason if not (brief and brief.published) else None  # published in time: complete
    fallback_reason: Optional[str] = None
    if brief is None:
        fallback_reason = s.error or "the strategy agent produced no accepted considerations"
    elif critique and critique["verdict"] == "revise":
        fallback_reason = "the critic still flagged issues: " + "; ".join(critique["issues"])

    # A life-safety contradiction always reaches the dispatcher, whether or not the agent planned it.
    asked = {q.field for q in copilot.read_questions(s.ws)}
    if any(c.key not in asked for c in copilot.unresolved_life_safety(_incident(s.ws))):
        copilot.write_questions(s.ws, _incident(s.ws))

    _ensure_overlay(s)
    s.status = "briefed"
    if fallback_reason:
        _ensure_dossier(s)
        _ensure_exterior_entries(s)
        brief = _write_brief(s, [], extra_gaps=(f"Considerations withheld: {fallback_reason}",), published=True)
    elif not brief.published:
        if critique is None:
            brief.gaps.insert(0, "Considerations were not checked by the critic")
        if partial:
            brief = publish(_drop_unreviewed(brief, partial))  # no review wait after a deadline
        elif low_confidence(brief):
            s.status = "awaiting_approval"  # with or without the agent paused at publish_brief
        else:
            brief = publish(brief)  # nothing to review: the agent just did not get to publish_brief
        s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
    if partial:
        brief.partial, brief.partial_reason = True, partial
        if fallback_reason != partial:  # facts only already says why in its gap
            brief.gaps.insert(0, f"Partial brief: {partial}")
        s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
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
    """Save fusion's record as the incident's incident.json, the new segments to its transcript.json
    (what the incident mapper cites), and both to Tiger.

    Call after every FusionEngine.ingest (pass the segments just ingested). workspace_root=None
    means MongoDB when configured, else workspace/.
    """
    new_segments = list(new_segments)
    ws = incident_workspace(record.incident_id, workspace_root)
    ws.write(INCIDENT, record.model_dump_json(indent=2))
    overlay_mod.append_transcript(ws, new_segments)
    db.save_segments(record.incident_id, new_segments)
    db.save_facts(record)


def start_incident(incident_id: str, workspace_root: Optional[Path] = None,
                   on_todos: Optional[Callable[[Progress], None]] = None,
                   on_overlay: Optional[Callable[[IncidentOverlay], None]] = None,
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

    ws.clear(keep=(INCIDENT, overlay_mod.TRANSCRIPT))  # earlier output for this incident (briefs, layers, notes, ...)
    ws.write(CANDIDATES, json.dumps([c.model_dump() for c in candidates], indent=2))

    saver = checkpointer or _checkpointer()
    saver.delete_thread(incident_id)  # one thread per incident; starting again starts it clean
    db.forget_briefs(incident_id)
    s = _SESSIONS[incident_id] = Session(incident_id=incident_id, ws=ws, candidates=candidates,
                                         on_todos=on_todos, on_overlay=on_overlay, use_cache=use_cache)
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
    s.leg_t0 = t0
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


def review_brief(incident_id: str, drop: Sequence[str] = ()) -> RunResult:
    """The dispatcher's answer on low-confidence considerations: publish the brief as it is
    (drop empty), or without the considerations whose ids are in `drop`."""
    t0 = time.perf_counter()
    s = _session(incident_id)
    s.leg_t0 = t0
    if s.status != "awaiting_approval":
        raise ValueError(f"Incident {incident_id} has no brief awaiting approval (status: {s.status})")
    drop = list(drop)
    if unknown := [d for d in drop if d not in {c.id for c in _brief(s).considerations}]:
        raise ValueError(f"{unknown} are not considerations of brief v{s.version} of incident {incident_id}")

    if _pending(s, "publish_brief"):
        # Built here, not taken from the caller: the tool name stays pinned.
        decision = ({"type": "edit", "edited_action": {"name": "publish_brief", "args": {"drop": drop}}}
                    if drop else {"type": "approve"})
        _run(s, Command(resume={"decisions": [decision]}), "review", t0)
    brief = _brief(s)
    if not brief.published:  # the agent was not paused at publish_brief, or its run failed: publish in code
        brief = publish(brief, drop)
        s.ws.write(s.brief_rel, brief.model_dump_json(indent=2))
    s.status = "briefed"
    return _result(s, t0, "review", brief, _critique(s), None)


def update_incident(incident_id: str, notes: list[str]) -> RunResult:
    """New information after a brief (fusion change notes). Same thread; writes the next brief version."""
    t0 = time.perf_counter()
    s = _session(incident_id)
    s.leg_t0 = t0
    if s.status == "awaiting_approval":
        raise ValueError(f"Incident {incident_id} has brief v{s.version} awaiting the dispatcher: "
                         "call review_brief first")
    if s.status != "briefed":
        raise ValueError(f"Incident {incident_id} has no brief to update yet (status: {s.status})")
    s.version, s.accepted, s.rejections = s.version + 1, 0, 0
    s.overlay_attempts, s.overlay_rejections = 0, 0
    if _incident(s.ws).confirmed_building_id != s.building_id:
        # Fusion rewrote incident.json from a record that was never told (FusionEngine.confirm).
        log.warning("Incident %s: incident.json lost its confirmed building; restoring %s",
                    incident_id, s.building_id)
        _confirm(s.ws, s.building_id)
    _refresh_dossier(s)  # crew feedback since the last brief changes what the strategy may use
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
    _ensure_exterior_entries(s)  # no interior scan: the cache carries the aerial entries too
    at = cache.stamp(building_id)
    hit = cache.load_fresh(building_id)
    seconds = time.perf_counter() - t0
    db.log_event(f"prefetch-{building_id}", "prefetch", seconds, building_id=building_id, error=s.error)
    return PrefetchResult(building_id=building_id, prefetched_at=at, workspace=folder,
                          notes=hit.notes if hit else {}, seconds=seconds)
