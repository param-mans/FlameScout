"""On-demand plans for catalog houses (data/raw/catalog.csv) that have no folder in data/raw yet.

The plan comes from the Node house data service (apps/web/server: GET /api/house-viewer/plan/<listing_id>), which queries
Matterport live (read-only) and adds the viewer's extras (plan-extras.mjs) and the plan index; nothing is recomputed here.
The answer is cached as data/raw/<listing_id>/matterport/plan.json (the viewer plan) and
data/raw/<listing_id>/derived/plan_index.json, the same paths the hero houses use, and read from there next time.

HOUSE_DATA_URL (env): the service's base URL, default http://127.0.0.1:8080 (standalone.mjs, HOUSE_DATA_PORT);
"off" disables fetching. Timeout 15 s. Any failure (no service, no keys, no plan, bad answer) returns False: the house is
treated as having no interior scan and the pipeline goes on. A failure is remembered for FAIL_TTL_S so a run does not
wait on the same dead service several times. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .. import house_catalog

log = logging.getLogger("flamescout.house_data")

DEFAULT_URL = "http://127.0.0.1:8080"
TIMEOUT_S = 15.0
FAIL_TTL_S = 120.0
_ID_RE = re.compile(r"[0-9]{1,20}")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_failed: dict[str, float] = {}   # "<root>|<id>" -> time of the last failure
stats = {"fetches": 0}           # service calls made (tests and logs)


def base_url() -> Optional[str]:
    url = os.getenv("HOUSE_DATA_URL", DEFAULT_URL).strip()
    return None if url.lower() in ("", "off", "0", "none") else url.rstrip("/")


def plan_paths(root: Path, listing_id: str) -> tuple[Path, Path]:
    folder = Path(root) / listing_id
    return folder / "matterport" / "plan.json", folder / "derived" / "plan_index.json"


def cached(root: Path, listing_id: str) -> bool:
    return all(p.exists() for p in plan_paths(root, listing_id))


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _valid(doc: dict, model_id: str) -> Optional[str]:
    """Why the service's answer cannot be cached, or None."""
    plan, index = doc.get("plan"), doc.get("plan_index")
    if not isinstance(plan, dict) or not isinstance(plan.get("floors"), list) or not plan["floors"]:
        return "no plan in the answer"
    if not isinstance(index, dict) or not isinstance(index.get("rooms"), dict) or not isinstance(index.get("floors"), list):
        return "no plan index in the answer"
    if doc.get("model_id") and doc["model_id"] != model_id:
        return "the answer is for another model"
    return None


def _fetch(listing_id: str, url: str) -> dict:
    stats["fetches"] += 1
    r = httpx.get(f"{url}/api/house-viewer/plan/{listing_id}", timeout=TIMEOUT_S)
    if r.status_code != 200:
        try:
            why = str(r.json().get("error", ""))[:200]
        except ValueError:
            why = ""
        raise RuntimeError(f"HTTP {r.status_code} {why}".strip())
    return r.json()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def ensure_plan(listing_id: Optional[str], root: Path) -> bool:
    """True when data/raw/<listing_id> has plan.json + plan_index.json (already, or fetched now for a catalog house).
    False for ids not in the catalog without a cached plan, and on any failure ("No interior scan on file")."""
    if not listing_id or not _ID_RE.fullmatch(listing_id):
        return False
    if cached(root, listing_id):
        return True
    model_id = house_catalog.model_id(listing_id)
    url = base_url()
    if model_id is None or url is None:
        return False
    key = f"{root}|{listing_id}"
    with _lock(key):
        if cached(root, listing_id):   # another thread fetched it while this one waited
            return True
        if time.monotonic() - _failed.get(key, -1e9) < FAIL_TTL_S:
            return False
        t0 = time.perf_counter()
        try:
            doc = _fetch(listing_id, url)
            if why := _valid(doc, model_id):
                raise RuntimeError(why)
            plan_path, index_path = plan_paths(root, listing_id)
            _write(plan_path, doc["plan"])
            _write(index_path, doc["plan_index"])
        except Exception as e:  # noqa: BLE001 - no service, no keys, no plan: no interior scan, never a crash
            _failed[key] = time.monotonic()
            log.warning("on-demand plan for %s failed (%s: %s): no interior scan on file", listing_id,
                        type(e).__name__, str(e)[:200])
            return False
        log.info("on-demand plan for %s (model %s) fetched and cached in %.2f s", listing_id, model_id,
                 time.perf_counter() - t0)
        return True


def forget_failures() -> None:
    _failed.clear()
