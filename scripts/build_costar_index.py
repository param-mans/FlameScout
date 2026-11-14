"""Build the normalized CoStar index from the hackathon kit (run once, a few seconds).

    python scripts/build_costar_index.py ~/Downloads/costar-hackathon-kit
    # then in .env: COSTAR_INDEX=data/costar/properties.jsonl

The output is licensed CoStar data: it lives under data/costar/ (gitignored).
--as-of defaults to the end of the kit's data window (kit index.md, "Scope").
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.agents.tools.costar_kit import build_index  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit", type=Path, help="path to costar-hackathon-kit/")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "costar" / "properties.jsonl")
    ap.add_argument("--as-of", default="2026-11-09")
    ap.add_argument("--no-tenants", action="store_true", help="skip the occupancy/company join (faster)")
    a = ap.parse_args()
    kit = a.kit.expanduser().resolve()
    if not (kit / "data" / "commercial-property.jsonl").exists():
        sys.exit(f"not a CoStar kit folder: {kit}")
    t0 = time.perf_counter()
    n = build_index(kit, a.out, a.as_of, with_tenants=not a.no_tenants)
    print(f"wrote {n} properties to {a.out} in {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
