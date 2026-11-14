"""Run one scripted incident end to end, the way the API will drive it.

    python scripts/run_incident.py S2 [--cold] [--drop-low]
    python scripts/run_incident.py S4B           (9700 Tanner Rd, hero 23296 in data/raw: no interior scan)
    python scripts/run_incident.py S4Q           (synthetic building with no interior scan)

  call 1 -> fusion -> start_incident   (agent plans, pauses for the dispatcher)
            simulated dispatcher picks the first candidate -> resume_incident -> brief_v1.json
  call 2 -> fusion change notes -> update_incident (same agent thread)      -> brief_v2.json
  ... one update per further call in the script.

When a brief has a consideration with confidence below 0.5 the agent pauses again before publishing;
the simulated dispatcher approves it (or drops the low-confidence ones with --drop-low) through
review_brief. Copilot questions for the dispatcher (copilot_questions.json) are printed as they appear.

Building data prefetched with scripts/prefetch.py (under 24 h old) is reused and the layer subagents
are skipped; --cold ignores that cache.

Writes the incident's workspace (MongoDB when MONGODB_URI is set, else workspace/<incident_id>/):
incident.json, candidates.json, layers/{costar,matterport,vexcel}.json, dossier.json, brief_vN.json,
critique_vN.json, todos.json and notes/*.md. With TIGER_DATABASE_URL set, also records segments, facts,
briefs and timings in Tiger and prints the time-to-brief stats.
"""
import argparse
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
from services.agents import copilot, db  # noqa: E402
from services.agents import overlay as overlay_mod  # noqa: E402
from services.agents.orchestrator import (RunResult, flush_traces, resume_incident,  # noqa: E402
                                          review_brief, save_incident, start_incident, update_incident)
from services.agents.workspace import incident_workspace  # noqa: E402

MARKS = {"completed": "x", "in_progress": "~", "pending": " "}

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("services.agents.orchestrator").setLevel(logging.INFO)

parser = argparse.ArgumentParser()
parser.add_argument("script_id", nargs="?", default="S2")
parser.add_argument("--cold", action="store_true", help="ignore prefetched building data (data/cache)")
parser.add_argument("--drop-low", action="store_true",
                    help="the simulated dispatcher drops low-confidence considerations instead of approving them")
args = parser.parse_args()

script_id = args.script_id.lower()
data = json.loads((ROOT / "data/fixtures/calls" / f"{script_id}.json").read_text())
segments = [CallSegment(**s) for s in data["segments"]]
call_ids = list(dict.fromkeys(s.call_id for s in segments))

eng = FusionEngine(f"inc_{script_id}")
incident_id = eng.record.incident_id
ws = incident_workspace(incident_id)
db.reset_incident(incident_id)  # a re-run of the same script starts its Tiger rows over (timings are kept)


def replay(call_id: str) -> list[str]:
    """Feed one call through fusion and save the record. Returns fusion's change notes."""
    call_segments = [s for s in segments if s.call_id == call_id]
    notes = [n for s in call_segments for n in eng.ingest(s)]
    save_incident(eng.record, call_segments)
    print(f"\n=== {call_id}: {len(notes)} change(s) from fusion")
    for n in notes:
        print(f"  {n}")
    return notes


def show_todos(p: Progress) -> None:  # where the API would push to the UI's progress strip
    if p.todos:
        print(f"  plan [{p.status}]: " + "  ".join(f"[{MARKS[t.status]}] {t.content}" for t in p.todos))


review_seconds: dict[int, float] = {}
legs: list[RunResult] = []  # every orchestrator call, for the stall count


def reviewed(r: RunResult) -> RunResult:
    """If the brief is held for low-confidence considerations, play the dispatcher."""
    if r.status != "awaiting_approval":
        return r
    print(f"\npaused for dispatcher (awaiting_approval) after {r.seconds:.1f} s. Low confidence:")
    for c in r.brief.considerations:
        if c.id in r.needs_review:
            print(f"  {c.id} [conf {c.confidence:.2f}] {c.title}")
    drop = r.needs_review if args.drop_low else []
    print("dispatcher " + (f"drops: {', '.join(drop)}" if drop else "approves them") + "\n")
    done = review_brief(incident_id, drop=drop)
    legs.append(done)
    review_seconds[done.version] = done.seconds
    return done


def report(r: RunResult) -> bool:
    brief = Brief.model_validate_json(ws.read(f"brief_v{r.version}.json"))  # raises if invalid
    print(f"\nbrief {brief.brief_id}: {len(brief.considerations)} consideration(s), "
          f"critic: {(r.critique or {}).get('verdict', 'did not run')}, "
          f"verifier_rejections: {brief.verifier_rejections}, published: {brief.published}")
    for c in brief.considerations:
        mark = " [cross-layer]" if c.cross_layer else ""
        refs = f"  refs {c.refs}" if c.refs else ""
        print(f"  {c.rank}. [{c.category}, conf {c.confidence:.2f}]{mark} {c.title}  {c.evidence_ids}{refs}")
    if r.fallback_reason:
        print(f"  FACTS-ONLY FALLBACK: {r.fallback_reason}")
    if brief.changes_since_last:
        print("changes since last:", *brief.changes_since_last, sep="\n  - ")
    print("gaps:", *brief.gaps, sep="\n  - ")
    for c in brief.data_contradictions:
        print(f"data contradiction: {c.message}  {[e for v in c.values for e in v.evidence_ids]}")
    for flag in (r.critique or {}).get("note_flags", []):
        print(f"critic flagged a {flag['layer']} note claim: {flag['claim']}")
    ov = r.overlay
    if ov is not None:
        print(f"overlay v{ov.version} (rejected submissions: {r.overlay_rejections}):")
        for name in ("occupants", "fire", "hazards", "blocked"):
            for e in getattr(ov, name):
                where = overlay_mod.describe(e.location) if name != "blocked" else e.ref_id
                group = f", group {e.group_id}" if e.group_id else ""
                print(f"  {name} {e.id} [{e.status}{group}] {where}  {e.evidence_ids}")
        for o in ov.objectives:
            print(f"  objective {o.id}: {o.type} {o.target.kind} {o.target.ref_id or f'floor {o.target.floor_reported}'} "
                  f"(priority {o.priority})")
        for line in ov.changes_since_last:
            print(f"  change: {line}")
    for q in copilot.read_questions(ws):
        print(f"copilot question [{q.status}]: {q.question}")
    return (brief.published and len(brief.considerations) <= 5
            and all(c.title.startswith("Consider ") and c.evidence_ids
                    and all(e in brief.evidence for e in c.evidence_ids) for c in brief.considerations))


# Call 1 -> the agent plans and pauses for the dispatcher.
replay(call_ids[0])
started = start_incident(incident_id, on_todos=show_todos, use_cache=not args.cold)
print(f"\npaused for dispatcher ({started.status}) after {started.seconds:.1f} s. Candidates:")
for c in started.candidates:
    print(f"  {c.building_id}: {c.name}, {c.address} (match {c.score})")

# Simulated dispatcher: picks the first candidate. Keep the fusion record in step.
choice = started.candidates[0].building_id
eng.confirm(choice)
print(f"dispatcher confirms: {choice}\n")
resumed = resume_incident(incident_id, choice)
legs += [started, resumed]
v1 = reviewed(resumed)
ok = report(v1)
warm = v1.brief.freshness["prefetched"]
print("building data:", f"prefetched at {v1.brief.freshness['prefetched_at']}" if warm else "pulled for this incident")
held = f" + {review_seconds[1]:.1f} s after the dispatcher's review" if 1 in review_seconds else ""
total = started.seconds + resumed.seconds + review_seconds.get(1, 0.0)
timings = [f"v1 ({'warm, prefetched' if warm else 'cold'}): {started.seconds:.1f} s to the confirmation pause + "
           f"{resumed.seconds:.1f} s from resume to brief{held} = {total:.1f} s"]
if resumed.overlay_seconds is not None:
    timings.append(f"v1 overlay: {resumed.overlay_seconds:.1f} s after resume (brief at {resumed.seconds:.1f} s)")

# Each later call -> same agent thread, next brief version.
for call_id in call_ids[1:]:
    notes = replay(call_id)
    if not notes:
        continue
    updated = update_incident(incident_id, notes)
    legs.append(updated)
    update = reviewed(updated)
    ok = report(update) and ok
    held = (f" + {review_seconds[update.version]:.1f} s after the dispatcher's review"
            if update.version in review_seconds else "")
    timings.append(f"v{update.version}: {updated.seconds:.1f} s{held} (data layers reused)")
    if updated.overlay_seconds is not None:
        timings.append(f"v{update.version} overlay: {updated.overlay_seconds:.1f} s after the update started "
                       f"(brief at {updated.seconds:.1f} s)")

flush_traces()
print(f"\nworkspace {ws.location}:", *ws.list(), sep="\n  ")
print("\nwall clock:", *timings, sep="\n  ")
print(f"model stalls (no answer in 12 s): {sum(r.model_stalls for r in legs)}, "
      f"retries: {sum(r.model_retries for r in legs)}")
print("LangSmith thread:", incident_id if v1.trace_run_id else "tracing off")
if db.enabled():
    print("Tiger time-to-brief (all runs):", db.time_to_brief_stats())
sys.exit(0 if ok else 1)
