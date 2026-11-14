"""Where an incident's working documents live: local files or MongoDB Atlas.

The orchestrator's tools, the deep agent's /workspace/ (read_file, write_file under notes/) and
the API all go through a Workspace, so they always see the same documents:

  incident.json, candidates.json, todos.json, layers/<layer>.json, dossier.json,
  brief_vN.json, critique_vN.json, notes/*.md

  MONGODB_URI set  -> StoreWorkspace on MongoDB (mongo_store.py), namespace ("incidents", <id>)
  otherwise        -> FileWorkspace under workspace/<incident_id>/ (as before)

The prefetch cache (cache.py) uses the same two kinds, namespace ("cache", <building_id>).
Paths are relative and use "/" ("layers/costar.json").
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from deepagents.backends import FilesystemBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.utils import create_file_data
from langgraph.store.base import BaseStore

from . import mongo_store

log = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "workspace"
_SAFE = re.compile(r"[A-Za-z0-9_.@+:~-]+")


def _check_id(name: str) -> str:
    if not _SAFE.fullmatch(name) or name in (".", ".."):
        raise ValueError(f"Bad id {name!r}")
    return name


class Workspace(ABC):
    """A small document store keyed by relative path."""

    @abstractmethod
    def read(self, rel: str) -> str:
        """The document's text. Raises FileNotFoundError when it does not exist."""

    @abstractmethod
    def write(self, rel: str, text: str) -> None:
        """Create or replace. Readers never see a half-written document."""

    @abstractmethod
    def delete(self, rel: str) -> None:
        """Remove if present."""

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """Relative paths starting with prefix, sorted."""

    @abstractmethod
    def modified(self, rel: str) -> Optional[str]:
        """An opaque marker that changes when the document is rewritten. None when missing."""

    @abstractmethod
    def backend(self) -> BackendProtocol:
        """The deepagents backend for this workspace, mounted at /workspace/."""

    @property
    @abstractmethod
    def location(self) -> str:
        """Human-readable location for logs and scripts."""

    def exists(self, rel: str) -> bool:
        return self.modified(rel) is not None

    def read_or_none(self, rel: str) -> Optional[str]:
        try:
            return self.read(rel)
        except FileNotFoundError:
            return None

    def clear(self, keep: tuple[str, ...] = ()) -> None:
        for rel in self.list():
            if rel not in keep:
                self.delete(rel)


class FileWorkspace(Workspace):
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"{rel!r} is outside the workspace")
        return path

    def read(self, rel: str) -> str:
        return self._path(rel).read_text()

    def write(self, rel: str, text: str) -> None:
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    def delete(self, rel: str) -> None:
        path = self._path(rel)
        path.unlink(missing_ok=True)
        for parent in path.parents:  # drop folders this left empty, so list() and the agent agree
            if parent == self.root.resolve() or not parent.is_dir() or any(parent.iterdir()):
                break
            parent.rmdir()

    def list(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        rels = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(r for r in rels if r.startswith(prefix) and not r.endswith(".tmp"))

    def modified(self, rel: str) -> Optional[str]:
        try:
            return str(self._path(rel).stat().st_mtime_ns)
        except FileNotFoundError:
            return None

    def backend(self) -> BackendProtocol:
        self.root.mkdir(parents=True, exist_ok=True)
        return FilesystemBackend(root_dir=self.root, virtual_mode=True)

    @property
    def location(self) -> str:
        return str(self.root)


class StoreWorkspace(Workspace):
    """Documents in a LangGraph BaseStore (MongoStore in production), in deepagents' file format."""

    def __init__(self, store: BaseStore, namespace: tuple[str, ...]):
        self.store, self.namespace = store, tuple(_check_id(p) for p in namespace)

    @staticmethod
    def _key(rel: str) -> str:
        rel = rel.strip("/")
        if not rel or ".." in rel.split("/"):
            raise ValueError(f"Bad workspace path {rel!r}")
        return "/" + rel

    def read(self, rel: str) -> str:
        item = self.store.get(self.namespace, self._key(rel))
        if item is None:
            raise FileNotFoundError(f"{self.location}/{rel.strip('/')}")
        content = item.value["content"]
        return "\n".join(content) if isinstance(content, list) else content

    def write(self, rel: str, text: str) -> None:
        key = self._key(rel)
        old = self.store.get(self.namespace, key)
        self.store.put(self.namespace, key, create_file_data(text, (old.value if old else {}).get("created_at")))

    def delete(self, rel: str) -> None:
        self.store.delete(self.namespace, self._key(rel))

    def list(self, prefix: str = "") -> list[str]:
        rels, offset = [], 0
        while page := self.store.search(self.namespace, limit=200, offset=offset):
            rels += [i.key.lstrip("/") for i in page if i.namespace == self.namespace]
            offset += len(page)
        return sorted(r for r in rels if r.startswith(prefix))

    def modified(self, rel: str) -> Optional[str]:
        item = self.store.get(self.namespace, self._key(rel))
        return None if item is None else f"{item.updated_at.isoformat()}|{item.value.get('modified_at')}"

    def backend(self) -> BackendProtocol:
        namespace = self.namespace
        return StoreBackend(store=self.store, namespace=lambda _rt: namespace)

    @property
    def location(self) -> str:
        return "mongodb:" + "/".join(self.namespace)


def incident_workspace(incident_id: str, root: Optional[Path] = None) -> Workspace:
    """The incident's workspace. An explicit root (tests, scripts) always means local files."""
    _check_id(incident_id)
    if root is None and mongo_store.enabled():
        return StoreWorkspace(mongo_store.default_store(), ("incidents", incident_id))
    return FileWorkspace(Path(root or WORKSPACE_ROOT) / incident_id)


# Alias for API code: open_workspace("inc_s2").read("brief_v1.json")
open_workspace = incident_workspace
