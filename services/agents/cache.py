"""Prefetched building data (PRD F13).

scripts/prefetch.py runs the three layer subagents ahead of time and saves what
they produce here; an incident at that building then reuses it instead of
pulling again, as long as it is under MAX_AGE old.

  data/cache/<building_id>/cache.json                       building_id, prefetched_at
  data/cache/<building_id>/layers/{costar,matterport,vexcel}.json
  data/cache/<building_id>/notes/<layer>_summary.md

Not committed (see .gitignore).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from flamescout_schemas import now

from .dossier import LAYERS, LayerData

log = logging.getLogger(__name__)

CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"
MAX_AGE = timedelta(hours=24)


@dataclass
class Prefetched:
    building_id: str
    prefetched_at: datetime
    layers: dict[str, LayerData]
    notes: dict[str, str]  # layer -> the subagent's summary, when it wrote one


def cache_dir(building_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", building_id):
        raise ValueError(f"Bad building id {building_id!r}")
    return CACHE_ROOT / building_id


def stamp(building_id: str, at: Optional[datetime] = None) -> datetime:
    """Mark the layers currently in the building's cache folder as prefetched now."""
    at = at or now()
    (cache_dir(building_id) / "cache.json").write_text(
        json.dumps({"building_id": building_id, "prefetched_at": at.isoformat()}, indent=2))
    return at


def load_fresh(building_id: str, max_age: timedelta = MAX_AGE) -> Optional[Prefetched]:
    """The cached layers, or None when there is no complete cache under max_age old."""
    folder = cache_dir(building_id)
    try:
        meta = json.loads((folder / "cache.json").read_text())
        at = datetime.fromisoformat(meta["prefetched_at"])
        layers = {name: LayerData.model_validate_json((folder / "layers" / f"{name}.json").read_text())
                  for name in LAYERS}
    except FileNotFoundError:
        return None
    except (ValueError, KeyError) as e:
        log.warning("Ignoring unreadable prefetch cache for %s: %s", building_id, e)
        return None
    age = now() - at
    if meta.get("building_id") != building_id or any(d.building_id != building_id for d in layers.values()):
        log.warning("Ignoring prefetch cache for %s: it is for another building", building_id)
        return None
    if not timedelta(0) <= age < max_age:
        log.info("Prefetch cache for %s is %s old; not using it", building_id, age)
        return None
    notes = {name: path.read_text() for name in LAYERS
             if (path := folder / "notes" / f"{name}_summary.md").exists()}
    return Prefetched(building_id=building_id, prefetched_at=at, layers=layers, notes=notes)
