"""Prefetch a building's data layers ahead of any incident (PRD F13).

    python scripts/prefetch.py fake_wilshire_tower

Runs the costar / matterport / vexcel subagents in parallel and caches what they pull under
data/cache/<building_id>/ with a timestamp. For 24 h, an incident confirmed at that building reuses
the cache and skips its layer phase (run_incident.py --cold ignores it).
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "schemas")]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from services.agents.orchestrator import flush_traces, prefetch_building  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("services.agents.orchestrator").setLevel(logging.INFO)

result = prefetch_building(sys.argv[1])
flush_traces()
for layer, note in result.notes.items():
    print(f"\n[{layer}]\n{note.strip()}")
print("\nfiles:", *sorted(str(p.relative_to(ROOT)) for p in result.path.rglob("*") if p.is_file()), sep="\n  ")
print(f"\nprefetched {result.building_id} at {result.prefetched_at.isoformat()} in {result.seconds:.1f} s")
