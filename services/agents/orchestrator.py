"""Per-incident orchestrator (deepagents).

One deep agent per incident, started only after the dispatcher has confirmed
the building. It reads workspace/<incident_id>/incident.json, plans with
write_todos and delegates to three subagents:

  dossier   load_dossier()            -> dossier.json   + notes/dossier_summary.md
  strategy  submit_considerations()   -> brief_v1.json  + notes/strategy_reasoning.md
  critic    submit_critique()         -> critique_v1.json (pass | revise)

The model plans, delegates, drafts considerations and critiques. The JSON
artifacts are written only by the tools above (deterministic, schema-checked);
the built-in filesystem tools can read /workspace/ but write only under
/workspace/notes/.

The todo list is exposed three ways: workspace/<incident_id>/todos.json
(rewritten on every change), the on_todos callback, and RunResult.todos.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.tools import BaseTool, tool

from flamescout_schemas import Brief, BuildingDossier, IncidentRecord, Progress, TodoItem, now

from . import dossier as dossier_mod
from .brief import ConsiderationDraft, build_brief, check_considerations, merge_evidence

log = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"
INCIDENT, DOSSIER, BRIEF, CRITIQUE, TODOS = ("incident.json", "dossier.json", "brief_v1.json",
                                             "critique_v1.json", "todos.json")
MAX_SUBMISSIONS = 3  # first brief + 2 regenerations (PRD FR-6)

ORCHESTRATOR_PROMPT = """You are the FlameScout incident orchestrator. A dispatcher has confirmed the \
building for a fire incident and the first-arriving crew needs an advisory brief before they arrive. \
Speed matters: use as few turns as possible and batch tool calls exactly as described.

The incident workspace is mounted at /workspace/. You never write files yourself; subagents do that.
Subagents cannot see this conversation, so every task description must be self-contained.

Do exactly this:
1. First turn, three tool calls together:
   - read_file("/workspace/incident.json", limit=1000)
   - write_todos with four todos: "Load building dossier" (in_progress), "Draft considerations",
     "Critique brief", "Brief ready" (all pending)
   - task(subagent_type="dossier"): ask it to load the dossier for this incident.
2. Next turn, two tool calls together: write_todos (dossier completed, draft in_progress) and
   task(subagent_type="strategy"): ask it to draft the considerations. Mention any unresolved
   contradiction between callers that you saw in incident.json.
3. Next turn, two tool calls together: write_todos (draft completed, critique in_progress) and
   task(subagent_type="critic"): ask it to check /workspace/brief_v1.json.
4. Only if the critic's verdict is "revise": task the strategy subagent again, quoting the critic's
   issues and saying which revision number this is, then task the critic again. At most 2 revisions.
5. write_todos with all four completed. Then reply with one line: the critic's verdict.

After step 1 never call read_file, ls, glob or grep: do not re-check the subagents' work yourself.
Never state facts about the building or the incident yourself, and never contact anyone."""

DOSSIER_PROMPT = """You assemble the building dossier for a confirmed fire incident. Use exactly three turns:
1. Call load_dossier() once. It saves /workspace/dossier.json and returns a digest.
2. Call write_file once to create /workspace/notes/dossier_summary.md: a short markdown summary for the
   crew-brief writer (use and stories, access and egress, hazards, exposures, what is unknown or
   unverified). At most 80 words. Keep the evidence ids in brackets next to each point. Use only
   what the digest says; never guess.
3. Reply with one sentence: what was loaded and what is unknown.
Do not call any other tool."""

STRATEGY_PROMPT = """You draft advisory considerations for the first-arriving fire crew. Use exactly three turns:
1. One turn, two tool calls together: read_file("/workspace/incident.json", limit=1000) and
   read_file("/workspace/dossier.json", limit=1000).
2. One turn, two tool calls together:
   - write_file("/workspace/notes/strategy_reasoning.md"): at most 60 words on what you prioritised
     and why, what you left out, and anything unresolved. On a revision, write
     /workspace/notes/strategy_reasoning_rev<N>.md instead.
   - submit_considerations with at most 5 considerations, most important first, each with a
     one-sentence rationale of at most 25 words.
3. Reply with one sentence. If submit_considerations answered "Rejected", fix exactly what it lists
   and resubmit first.

Rules for every consideration:
- The title starts with "Consider " and is advisory. Never give an order.
- evidence_ids are copied exactly from the keys of the "evidence" objects in the two files, and each
  one actually supports the consideration. No evidence, no consideration.
- Use only facts in the two files. Never guess or add outside knowledge about this building.
- Caller reports are unconfirmed: say "reported". Where callers contradict each other, do not pick a
  side; word the consideration so it holds for both, or say it is unresolved.
- Do not build a consideration on a fact whose status is "unverified"."""

CRITIC_PROMPT = """You are the critic for a fire-crew advisory brief. Use exactly three turns:
1. read_file("/workspace/brief_v1.json", limit=3000).
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


@dataclass
class RunResult:
    incident_id: str
    brief: Brief
    todos: list[TodoItem]
    critique: Optional[dict]
    fallback_reason: Optional[str]   # set when the brief is facts-only
    seconds: float
    trace_run_id: Optional[str]      # LangSmith run id, None when tracing is off


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


def _critique(ws: Path) -> Optional[dict]:
    path = ws / CRITIQUE
    return json.loads(path.read_text()) if path.exists() else None


# ---------- tools (the only writers of the JSON artifacts) ----------

def make_tools(ws: Path) -> dict[str, BaseTool]:
    accepted = 0

    @tool
    def load_dossier() -> str:
        """Load the dossier for this incident's confirmed building and save it as /workspace/dossier.json.

        Returns a digest of the dossier with the evidence ids for every fact and marker.
        """
        try:
            d = dossier_mod.load_dossier(_incident(ws).confirmed_building_id or "")
        except (OSError, ValueError) as e:
            return f"Error: {e}"
        _write(ws / DOSSIER, d.model_dump_json(indent=2))
        return dossier_mod.digest(d)

    @tool(parse_docstring=True)
    def submit_considerations(considerations: list[ConsiderationDraft]) -> str:
        """Submit the ranked considerations. If they pass the checks, /workspace/brief_v1.json is written.

        Args:
            considerations: At most 5, most important first. Each title starts with "Consider ".
        """
        nonlocal accepted
        if accepted >= MAX_SUBMISSIONS:
            return "Error: regeneration budget used up. Do not resubmit."
        if not (ws / DOSSIER).exists():
            return "Error: /workspace/dossier.json does not exist yet. The dossier subagent must run first."
        incident, dossier = _incident(ws), _dossier(ws)
        if errors := check_considerations(considerations, merge_evidence(incident, dossier)):
            return "Rejected, nothing written. Fix these and resubmit:\n- " + "\n- ".join(errors)
        brief = build_brief(incident, dossier, considerations)
        _write(ws / BRIEF, brief.model_dump_json(indent=2))
        (ws / CRITIQUE).unlink(missing_ok=True)  # any earlier critique is about an older brief
        accepted += 1
        return f"Accepted: /workspace/brief_v1.json written with {len(considerations)} considerations."

    @tool(parse_docstring=True)  # not return_direct: task() would hand the orchestrator an empty result
    def submit_critique(verdict: Literal["pass", "revise"], issues: list[str]) -> str:
        """Record the verdict on /workspace/brief_v1.json as /workspace/critique_v1.json.

        Args:
            verdict: "pass" if every consideration is advisory and supported, otherwise "revise".
            issues: One specific issue per problem, naming the consideration id. Empty for "pass".
        """
        if not (ws / BRIEF).exists():
            return "Error: /workspace/brief_v1.json does not exist yet."
        if verdict == "revise" and not issues:
            return 'Error: "revise" needs at least one issue.'
        brief = Brief.model_validate_json((ws / BRIEF).read_text())
        _write(ws / CRITIQUE, json.dumps({"brief_id": brief.brief_id, "verdict": verdict, "issues": issues,
                                          "checked_at": now().isoformat()}, indent=2))
        return f"Recorded verdict: {verdict}."

    return {t.name: t for t in (load_dossier, submit_considerations, submit_critique)}


# ---------- agent ----------

def _model():
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=os.environ["GEMINI_FAST_MODEL"],
        google_api_key=os.environ["GEMINI_API_KEY"],
        thinking_level="low",  # default thinking roughly doubles every turn's latency
    )


def build_orchestrator(ws: Path, model: Any = None):
    tools = make_tools(ws)
    subagents = [
        {"name": "dossier",
         "description": "Loads the confirmed building's dossier into the workspace and summarises it.",
         "system_prompt": DOSSIER_PROMPT, "tools": [tools["load_dossier"]]},
        {"name": "strategy",
         "description": "Drafts up to 5 advisory considerations from incident.json and dossier.json and "
                        "writes brief_v1.json. Needs the dossier to exist. Can revise given critic issues.",
         "system_prompt": STRATEGY_PROMPT, "tools": [tools["submit_considerations"]]},
        {"name": "critic",
         "description": "Checks brief_v1.json: advisory wording, evidence support. Returns pass or revise.",
         "system_prompt": CRITIC_PROMPT, "tools": [tools["submit_critique"]]},
    ]
    return create_deep_agent(
        model=model or _model(),
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[TodoListMiddleware()],  # write_todos is opt-in in deepagents 0.7
        subagents=subagents,
        # Only /workspace/ is real disk; offloaded tool results etc. stay in ephemeral state.
        backend=CompositeBackend(default=StateBackend(),
                                 routes={"/workspace/": FilesystemBackend(root_dir=ws, virtual_mode=True)}),
        # First match wins: the model may write notes, nothing else in the workspace.
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/workspace/notes/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="deny"),
        ],
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


def _facts_only_brief(ws: Path, incident: IncidentRecord, reason: str) -> Brief:
    """Safe fallback (PRD FR-6): everything deterministic, no considerations."""
    if not (ws / DOSSIER).exists():
        d = dossier_mod.load_dossier(incident.confirmed_building_id or "")
        _write(ws / DOSSIER, d.model_dump_json(indent=2))
    brief = build_brief(incident, _dossier(ws), [], extra_gaps=(f"Considerations withheld: {reason}",))
    _write(ws / BRIEF, brief.model_dump_json(indent=2))
    return brief


def run_incident(incident_id: str, workspace_root: Path = WORKSPACE,
                 on_todos: Optional[Callable[[Progress], None]] = None,
                 model: Any = None) -> RunResult:
    """Run the orchestrator for one confirmed incident and return its brief."""
    t0 = time.perf_counter()
    ws = Path(workspace_root) / incident_id
    incident = _incident(ws)
    if not incident.confirmed_building_id:
        raise ValueError(f"Incident {incident_id} has no confirmed building; the dispatcher must confirm first")

    for stale in (DOSSIER, BRIEF, CRITIQUE, TODOS):
        (ws / stale).unlink(missing_ok=True)
    shutil.rmtree(ws / "notes", ignore_errors=True)

    run_id = uuid.uuid4()
    trace_run_id = str(run_id) if _enable_tracing() else None
    todos: list[TodoItem] = []

    def publish(raw: list[dict]) -> None:
        nonlocal todos
        todos = [TodoItem(**t) for t in raw]
        progress = Progress(incident_id=incident_id, todos=todos, trace_run_id=trace_run_id)
        _write(ws / TODOS, progress.model_dump_json(indent=2))
        if on_todos:
            on_todos(progress)

    publish([])
    error: Optional[str] = None
    try:
        agent = build_orchestrator(ws, model)
        prompt = (f"Incident {incident_id}: the dispatcher confirmed building "
                  f"{incident.confirmed_building_id}. Produce the first brief.")
        config = {"run_id": run_id, "run_name": f"incident:{incident_id}", "recursion_limit": 60,
                  "tags": ["flamescout", "orchestrator"],
                  "metadata": {"incident_id": incident_id, "building_id": incident.confirmed_building_id}}
        for chunk in agent.stream({"messages": [{"role": "user", "content": prompt}]}, config,
                                  stream_mode="updates", subgraphs=True, version="v2"):
            if chunk["type"] != "updates":
                continue
            for update in chunk["data"].values():
                if not isinstance(update, dict):
                    continue
                _log_activity(chunk["ns"], update, t0)
                if not chunk["ns"] and "todos" in update:  # the orchestrator's own plan
                    publish(update["todos"])
    except Exception as e:  # the crew still gets the facts
        log.exception("Orchestrator run failed for %s", incident_id)
        error = f"the orchestrator run failed ({type(e).__name__})"

    critique = _critique(ws)
    fallback_reason: Optional[str] = None
    if not (ws / BRIEF).exists():
        fallback_reason = error or "the strategy agent produced no accepted considerations"
    elif critique and critique["verdict"] == "revise":
        fallback_reason = "the critic still flagged issues: " + "; ".join(critique["issues"])

    if fallback_reason:
        brief = _facts_only_brief(ws, incident, fallback_reason)
    else:
        brief = Brief.model_validate_json((ws / BRIEF).read_text())
        if critique is None:
            brief.gaps.insert(0, "Considerations were not checked by the critic")
            _write(ws / BRIEF, brief.model_dump_json(indent=2))

    return RunResult(incident_id=incident_id, brief=brief, todos=todos, critique=critique,
                     fallback_reason=fallback_reason, seconds=time.perf_counter() - t0,
                     trace_run_id=trace_run_id)
