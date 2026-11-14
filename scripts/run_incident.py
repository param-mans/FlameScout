"""Run one scripted incident end to end, the way the API will drive it.

    python scripts/run_incident.py S2

  call 1 -> fusion -> start_incident   (agent plans, pauses for the dispatcher)
            simulated dispatcher picks the first candidate -> resume_incident -> brief_v1.json
  call 2 -> fusion change notes -> update_incident (same agent thread)      -> brief_v2.json
  ... one update per further call in the script.

Writes workspace/<incident_id>/: incident.json, candidates.json, layers/{costar,matterport,vexcel}.json,
dossier.json, brief_vN.json, critique_vN.json, todos.json and notes/*.md.
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "schemas")]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # GEMINI_*, LANGSMITH_*, EXTRACTOR - before anything reads the env

from flamescout_schemas import Brief, CallSegment, Progress  # noqa: E402
from services.agents.fusion import FusionEngine  # noqa: E402
from services.agents.orchestrator import (RunResult, flush_traces, resume_incident,  # noqa: E402
                                          start_incident, update_incident)

MARKS = {"completed": "x", "in_progress": "~", "pending": " "}

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("services.agents.orchestrator").setLevel(logging.INFO)

script_id = (sys.argv[1] if len(sys.argv) > 1 else "S2").lower()
data = json.loads((ROOT / "data/fixtures/calls" / f"{script_id}.json").read_text())
segments = [CallSegment(**s) for s in data["segments"]]
call_ids = list(dict.fromkeys(s.call_id for s in segments))

eng = FusionEngine(f"inc_{script_id}")
incident_id = eng.record.incident_id
ws = ROOT / "workspace" / incident_id
ws.mkdir(parents=True, exist_ok=True)


def replay(call_id: str) -> list[str]:
    """Feed one call through fusion and save the record. Returns fusion's change notes."""
    notes = [n for s in segments if s.call_id == call_id for n in eng.ingest(s)]
    (ws / "incident.json").write_text(eng.record.model_dump_json(indent=2))
    print(f"\n=== {call_id}: {len(notes)} change(s) from fusion")
    for n in notes:
        print(f"  {n}")
    return notes


def show_todos(p: Progress) -> None:  # where the API would push to the UI's progress strip
    if p.todos:
        print(f"  plan [{p.status}]: " + "  ".join(f"[{MARKS[t.status]}] {t.content}" for t in p.todos))


def report(r: RunResult) -> bool:
    brief = Brief.model_validate_json((ws / f"brief_v{r.version}.json").read_text())  # raises if invalid
    print(f"\nbrief {brief.brief_id}: {len(brief.considerations)} consideration(s), "
          f"critic: {(r.critique or {}).get('verdict', 'did not run')}, "
          f"verifier_rejections: {brief.verifier_rejections}")
    for c in brief.considerations:
        mark = " [cross-layer]" if c.cross_layer else ""
        print(f"  {c.rank}. [{c.category}, conf {c.confidence:.2f}]{mark} {c.title}  {c.evidence_ids}")
    if r.fallback_reason:
        print(f"  FACTS-ONLY FALLBACK: {r.fallback_reason}")
    if brief.changes_since_last:
        print("changes since last:", *brief.changes_since_last, sep="\n  - ")
    return (len(brief.considerations) <= 5
            and all(c.title.startswith("Consider ") and c.evidence_ids
                    and all(e in brief.evidence for e in c.evidence_ids) for c in brief.considerations))


# Call 1 -> the agent plans and pauses for the dispatcher.
replay(call_ids[0])
started = start_incident(incident_id, on_todos=show_todos)
print(f"\npaused for dispatcher ({started.status}) after {started.seconds:.1f} s. Candidates:")
for c in started.candidates:
    print(f"  {c.building_id}: {c.name}, {c.address} (match {c.score})")

# Simulated dispatcher: picks the first candidate. Keep the fusion record in step.
choice = started.candidates[0].building_id
eng.confirm(choice)
print(f"dispatcher confirms: {choice}\n")
v1 = resume_incident(incident_id, choice)
ok = report(v1)
timings = [f"v1: {started.seconds:.1f} s to the confirmation pause + {v1.seconds:.1f} s from resume to brief "
           f"= {started.seconds + v1.seconds:.1f} s"]

# Each later call -> same agent thread, next brief version.
for call_id in call_ids[1:]:
    notes = replay(call_id)
    if not notes:
        continue
    update = update_incident(incident_id, notes)
    ok = report(update) and ok
    timings.append(f"v{update.version}: {update.seconds:.1f} s (data layers reused)")

flush_traces()
print("\nfiles:", *sorted(str(p.relative_to(ROOT)) for p in ws.rglob("*") if p.is_file()), sep="\n  ")
print("\nwall clock:", *timings, sep="\n  ")
print("LangSmith thread:", incident_id if v1.trace_run_id else "tracing off")
sys.exit(0 if ok else 1)
