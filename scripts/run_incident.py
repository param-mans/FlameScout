"""Run one scripted incident end to end: calls -> fusion -> confirm building -> orchestrator -> brief.

    python scripts/run_incident.py S2 [--building fake_wilshire_tower]
Writes workspace/<incident_id>/: incident.json, dossier.json, brief_v1.json, critique_v1.json,
todos.json and notes/*.md.
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
from services.agents.orchestrator import flush_traces, run_incident  # noqa: E402

MARKS = {"completed": "x", "in_progress": "~", "pending": " "}

parser = argparse.ArgumentParser()
parser.add_argument("script_id", nargs="?", default="S2")
parser.add_argument("--building", default="fake_wilshire_tower",
                    help="building the simulated dispatcher confirms")
args = parser.parse_args()

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("services.agents.orchestrator").setLevel(logging.INFO)

# 1. Calls -> incident record
script_id = args.script_id.lower()
data = json.loads((ROOT / "data/fixtures/calls" / f"{script_id}.json").read_text())
eng = FusionEngine(f"inc_{script_id}")
eng.ingest_all(CallSegment(**s) for s in data["segments"])

# 2. Simulated dispatcher confirms the building. The orchestrator refuses to start without this.
eng.record.confirmed_building_id = args.building
eng.record.status = "confirmed"
ws = ROOT / "workspace" / eng.record.incident_id
ws.mkdir(parents=True, exist_ok=True)
(ws / "incident.json").write_text(eng.record.model_dump_json(indent=2))
print(f"{eng.record.incident_id}: {len(eng.record.facts)} facts, "
      f"{len(eng.record.contradictions)} contradiction(s), building confirmed: {args.building}\n")


# 3. Orchestrator. on_todos is where the API would push to the UI's progress strip.
def show_todos(p: Progress) -> None:
    if p.todos:
        print("  plan: " + "  ".join(f"[{MARKS[t.status]}] {t.content}" for t in p.todos))


result = run_incident(eng.record.incident_id, on_todos=show_todos)
flush_traces()

# 4. Report
brief = Brief.model_validate_json((ws / "brief_v1.json").read_text())  # raises if the file is invalid
bad = [c.id for c in brief.considerations
       if not c.title.startswith("Consider ") or not c.evidence_ids
       or any(e not in brief.evidence for e in c.evidence_ids)]
print(f"\nbrief {brief.brief_id}: {len(brief.considerations)} consideration(s), "
      f"critic: {(result.critique or {}).get('verdict', 'did not run')}")
for c in brief.considerations:
    print(f"  {c.rank}. [{c.category}, conf {c.confidence:.2f}] {c.title}  {c.evidence_ids}")
if result.fallback_reason:
    print(f"  FACTS-ONLY FALLBACK: {result.fallback_reason}")
print("gaps:", *brief.gaps, sep="\n  - ")
print("\nfiles:", *sorted(str(p.relative_to(ROOT)) for p in ws.rglob("*") if p.is_file()), sep="\n  ")
print(f"\nrun_incident wall clock: {result.seconds:.1f} s")
print("LangSmith run:", result.trace_run_id or "tracing off")
sys.exit(1 if bad or len(brief.considerations) > 5 else 0)
