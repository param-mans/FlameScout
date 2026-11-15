"""Download a hero building's Vexcel images (the ortho and the four obliques, Vexcel API 2.0) and read
them once with Gemini vision.

    python scripts/fetch_vexcel.py 209217448 [23296 ...]     download, then read
    python scripts/fetch_vexcel.py --read-only 209217448      read the stored images again (new prompt / model)

Replays the read-only extract requests whose parameters data/raw/<id>/vexcel/metadata.json stored
(the same GET /ortho/extract and /oriented/extract calls as apps/web/server/vexcel.mjs). The key
travels only in the X-API-KEY header and is never printed. Writes data/raw/<id>/vexcel/ortho.jpg,
oblique-<dir>.jpg and images.json (what was fetched, when, with which parameters). The reading
(services/agents/aerial.py) goes to data/raw/<id>/derived/aerial_reading.json; the Vexcel provider turns it into
markers, exposures and facts with no further model call. Sponsor data: data/raw is gitignored, never commit it.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "schemas")]
load_dotenv(ROOT / ".env")

from services.agents import aerial  # noqa: E402

BASE = "https://api.vexcelgroup.com/v2"
COPYRIGHT = "© Vexcel Imaging"


def extract(key: str, route: str, params: dict) -> tuple[int, bytes | None]:
    r = requests.get(f"{BASE}{route}", params=params, headers={"X-API-KEY": key, "Accept": "*/*"}, timeout=90)
    if r.status_code in (401, 403):
        sys.exit(f"Vexcel rejected the key (HTTP {r.status_code})")
    jpeg = r.ok and r.content[:2] == b"\xff\xd8"
    return r.status_code, r.content if jpeg else None


def fetch(building_id: str, key: str) -> None:
    folder = ROOT / "data" / "raw" / building_id / "vexcel"
    meta = json.loads((folder / "metadata.json").read_text())
    views = [("ortho", "/ortho/extract", meta.get("ortho") or {})]
    views += [(f"oblique-{d}", "/oriented/extract", o) for d, o in (meta.get("oblique") or {}).items()]
    images = {}
    for name, route, v in views:
        if not v.get("params"):
            continue
        status, body = extract(key, route, v["params"])
        print(f"{building_id} {name}: HTTP {status}" + (f", {len(body)} bytes" if body else ", no image"))
        if body:
            (folder / f"{name}.jpg").write_bytes(body)
            images[name] = {"file": f"{name}.jpg", "route": route, "params": v["params"],
                            "captureDate": v.get("captureDate"), "bbox_meters": v.get("bbox_meters")}
    (folder / "images.json").write_text(json.dumps(
        {"source": "Vexcel API 2.0 (read-only extract requests replayed from metadata.json params)",
         "copyright": COPYRIGHT, "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "convention": meta.get("convention"), "images": images}, indent=1))


def show(building_id: str, reading: dict | None) -> None:
    if reading is None:
        print(f"{building_id}: not read (no stored images, or no vision model configured)")
        return
    for view, r in reading["views"].items():
        print(f"{building_id} {view}: " + json.dumps({k: v for k, v in r.items() if v not in ([], None)})[:400])


if __name__ == "__main__":
    args = sys.argv[1:]
    read_only = "--read-only" in args
    ids = [a for a in args if not a.startswith("--")]
    if not ids:
        sys.exit(__doc__)
    # an empty VEXCEL_API_KEY exported by the shell would hide the .env value (load_dotenv never overrides)
    key = (os.environ.get("VEXCEL_API_KEY") or dotenv_values(ROOT / ".env").get("VEXCEL_API_KEY") or "").strip()
    if not key and not read_only:
        sys.exit("VEXCEL_API_KEY is not set in .env")
    for bid in ids:
        if not read_only:
            fetch(bid, key)
        show(bid, aerial.read_building(bid, force=True))
