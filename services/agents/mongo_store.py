"""A LangGraph BaseStore on MongoDB Atlas: the incident workspaces and the prefetch cache.

deepagents' StoreBackend reads and writes the agent's /workspace/ through this store, and
workspace.StoreWorkspace (our tools, the API) reads and writes the same documents. One document
per file in the `workspace_files` collection:

  _id         "<ns joined by '/'>|<key>"            e.g. "incidents/inc_s2|/brief_v1.json"
  ns          ["incidents", "inc_s2"]               or ["cache", "<building_id>"]
  key         "/brief_v1.json"                      path inside the workspace
  value       {content, encoding, created_at, modified_at}   (deepagents FileData)
  doc         the parsed JSON, for *.json files that parse   (query it in Atlas; never read back)
  created_at, updated_at

Enabled when MONGODB_URI is set (database MONGODB_DB, default "flamescout").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from langgraph.store.base import (BaseStore, GetOp, Item, ListNamespacesOp, Op, PutOp, Result,
                                  SearchItem, SearchOp)

log = logging.getLogger(__name__)

COLLECTION = "workspace_files"


def enabled() -> bool:
    return bool(os.environ.get("MONGODB_URI"))


def _id(namespace: tuple[str, ...], key: str) -> str:
    return "/".join(namespace) + "|" + key  # namespace parts never contain "/" (StoreBackend validates)


def _parsed(key: str, value: dict) -> Any:
    if not key.endswith(".json") or not isinstance(value.get("content"), str):
        return None
    try:
        return json.loads(value["content"])
    except ValueError:
        return None


class MongoStore(BaseStore):
    """Exact-key get/put/delete and namespace-prefix search. No vector search, no TTL."""

    def __init__(self, collection):
        self.col = collection
        try:
            self.col.create_index([("ns", 1), ("key", 1)])
        except Exception as e:  # an index is an optimisation; never block on it
            log.warning("MongoDB: could not create the workspace index: %s", e)

    # ---------- BaseStore ----------
    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return [self._op(op) for op in ops]

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await asyncio.to_thread(self.batch, list(ops))

    # ---------- ops ----------
    def _op(self, op: Op) -> Result:
        if isinstance(op, GetOp):
            doc = self.col.find_one({"_id": _id(op.namespace, op.key)})
            return self._item(doc) if doc else None
        if isinstance(op, PutOp):
            self._put(op)
            return None
        if isinstance(op, SearchOp):
            return self._search(op)
        if isinstance(op, ListNamespacesOp):
            return self._namespaces(op)
        raise NotImplementedError(f"MongoStore does not support {type(op).__name__}")

    def _put(self, op: PutOp) -> None:
        _id_ = _id(op.namespace, op.key)
        if op.value is None:
            self.col.delete_one({"_id": _id_})
            return
        at = datetime.now(timezone.utc)
        self.col.update_one(
            {"_id": _id_},
            {"$set": {"ns": list(op.namespace), "key": op.key, "value": op.value,
                      "doc": _parsed(op.key, op.value), "updated_at": at},
             "$setOnInsert": {"created_at": at}},
            upsert=True)

    def _search(self, op: SearchOp) -> list[SearchItem]:
        if op.query:
            log.warning("MongoStore: natural-language search is not supported; ignoring the query")
        query: dict[str, Any] = {f"ns.{i}": part for i, part in enumerate(op.namespace_prefix)}
        query.update({f"value.{k}": v for k, v in (op.filter or {}).items()})
        docs = self.col.find(query).sort("_id", 1).skip(op.offset).limit(op.limit)
        return [SearchItem(**vars_(self._item(d))) for d in docs]

    def _namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        seen = sorted({tuple(d["ns"]) for d in self.col.find({}, {"ns": 1})})
        if op.max_depth is not None:
            seen = sorted({ns[:op.max_depth] for ns in seen})
        for cond in op.match_conditions or ():
            path = tuple(cond.path)
            match = (lambda ns: ns[:len(path)]) if cond.match_type == "prefix" else (lambda ns: ns[-len(path):])
            seen = [ns for ns in seen if all(p in ("*", n) for p, n in zip(path, match(ns)))
                    and len(ns) >= len(path)]
        return seen[op.offset:op.offset + op.limit]

    @staticmethod
    def _item(doc: dict) -> Item:
        return Item(value=doc["value"], key=doc["key"], namespace=tuple(doc["ns"]),
                    created_at=doc.get("created_at") or doc["updated_at"], updated_at=doc["updated_at"])


def vars_(item: Item) -> dict:
    return {"value": item.value, "key": item.key, "namespace": item.namespace,
            "created_at": item.created_at, "updated_at": item.updated_at}


_STORE: Optional[MongoStore] = None


def default_store() -> MongoStore:
    """The process-wide store on MONGODB_URI (one client, connection pooling inside pymongo)."""
    global _STORE
    if _STORE is None:
        from pymongo import MongoClient

        client = MongoClient(os.environ["MONGODB_URI"], appname="flamescout", serverSelectionTimeoutMS=8000,
                             tz_aware=True)
        _STORE = MongoStore(client[os.environ.get("MONGODB_DB", "flamescout")][COLLECTION])
    return _STORE


def reset() -> None:
    """Forget the process-wide store (tests)."""
    global _STORE
    _STORE = None
