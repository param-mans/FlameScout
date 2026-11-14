"""JSON-file workspace (CLAUDE.md: workspace/<incident_id>/ is the deepagent filesystem
AND what the UI renders).

workspace/
  <incident_id>/incident.json      IncidentRecord (latest)
               /inputs.jsonl       segments + dispatcher resolutions, in order (replay log)
               /candidates.json    [LocationCandidate]
               /dossier.json       BuildingDossier for the confirmed building
               /brief_v<N>.json    Brief versions
               /copilot.json       {suggestion_id: "dismissed"|"asked"}
               /feedback.jsonl     CrewFeedback
               /audit.jsonl        who viewed / changed what
  _cache/dossiers/<building_id>.json, _cache/vision/
  _buildings/<building_id>/feedback.jsonl   crew notes that follow the building
  _metrics/timings.jsonl
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NotFound(KeyError):
    pass


def check_id(value: str) -> str:
    if not SAFE_ID.match(value or ""):
        raise ValueError(f"invalid id: {value!r}")
    return value


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    # ---------- paths ----------
    def incident_dir(self, incident_id: str) -> Path:
        return self.root / check_id(incident_id)

    def building_dir(self, building_id: str) -> Path:
        return self.root / "_buildings" / check_id(building_id)

    def cache_dir(self, name: str) -> Path:
        return self.root / "_cache" / name

    def metrics_file(self) -> Path:
        return self.root / "_metrics" / "timings.jsonl"

    # ---------- primitives ----------
    @staticmethod
    def write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = obj.model_dump_json(indent=2) if isinstance(obj, BaseModel) else json.dumps(obj, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)     # atomic: the UI never reads a half-written file

    @staticmethod
    def read_json(path: Path, model: Optional[type[T]] = None) -> Any:
        if not path.exists():
            raise NotFound(str(path))
        data = json.loads(path.read_text())
        return model.model_validate(data) if model else data

    @staticmethod
    def append_jsonl(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj, default=str)
        with path.open("a") as f:
            f.write(line + "\n")

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # ---------- incidents ----------
    def incident_ids(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/incident.json")
                      if not p.parent.name.startswith("_"))

    def incident_exists(self, incident_id: str) -> bool:
        return (self.incident_dir(incident_id) / "incident.json").exists()

    def delete_incident(self, incident_id: str) -> None:
        d = self.incident_dir(incident_id)
        if not d.exists():
            raise NotFound(incident_id)
        shutil.rmtree(d)

    def brief_versions(self, incident_id: str) -> list[int]:
        d = self.incident_dir(incident_id)
        return sorted(int(m.group(1)) for p in d.glob("brief_v*.json")
                      if (m := re.fullmatch(r"brief_v(\d+)\.json", p.name)))

    def brief_path(self, incident_id: str, version: int) -> Path:
        return self.incident_dir(incident_id) / f"brief_v{version}.json"

    def read_many(self, paths: Iterable[Path], model: type[T]) -> list[T]:
        return [self.read_json(p, model) for p in paths]
