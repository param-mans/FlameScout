"""Replay a call script through fusion and write the IncidentRecord.

    python scripts/run_fusion.py S2
Writes workspace/<incident_id>/incident.json (the file the UI / orchestrator reads).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "schemas")]

from flamescout_schemas import CallSegment  # noqa: E402
from services.agents.fusion import FusionEngine  # noqa: E402

script_id = (sys.argv[1] if len(sys.argv) > 1 else "S2").upper()
data = json.loads((ROOT / "data/fixtures/calls" / f"{script_id.lower()}.json").read_text())
eng = FusionEngine(f"inc_{script_id.lower()}")
for s in data["segments"]:
    seg = CallSegment(**s)
    for note in eng.ingest(seg):
        print(f"[{seg.call_id} {seg.t_start:>5.1f}s] {note}")

out = ROOT / "workspace" / eng.record.incident_id / "incident.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(eng.record.model_dump_json(indent=2))
print(f"\nready_to_brief={eng.record.ready_to_brief}  contradictions={len(eng.record.contradictions)}")
print("open questions:", *eng.record.open_questions, sep="\n  - ")
print(f"\nwrote {out.relative_to(ROOT)}")
