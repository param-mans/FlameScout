import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from flamescout_schemas import CallSegment

load_dotenv()  # GEMINI_* / LANGSMITH_* from .env, for the live tests

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "fixtures"


def load_script(script_id: str) -> list[CallSegment]:
    data = json.loads((FIXTURES / "calls" / f"{script_id.lower()}.json").read_text())
    return [CallSegment(**s) for s in data["segments"]]


def pytest_collection_modifyitems(config, items):
    """@pytest.mark.live tests call real APIs: opt in with RUN_LIVE=1."""
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _offline_by_default(request, monkeypatch, tmp_path):
    """Unless a test is marked live: keyword extractor, no LangSmith uploads and no databases (Tiger,
    MongoDB), whatever .env says. Every test gets an empty prefetch cache, so a real data/cache
    never changes a test's path."""
    from langsmith import utils

    from services.agents import cache, db, memory, mongo_store

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "prefetch_cache")
    monkeypatch.setattr(memory, "MEMORY_ROOT", tmp_path / "building_memory")  # no memory from real runs
    if "live" not in request.keywords:
        monkeypatch.setenv("EXTRACTOR", "keyword")
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
        monkeypatch.delenv("TIGER_DATABASE_URL", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        for k in ("GEMINI_API_KEY", "GEOCODER", "COSTAR_INDEX"):  # services/api tests: no geocoder, no kit
            monkeypatch.delenv(k, raising=False)
    utils.get_env_var.cache_clear()  # langsmith caches env lookups
    db.reset()
    mongo_store.reset()
    yield
    utils.get_env_var.cache_clear()
    db.reset()
    mongo_store.reset()


@pytest.fixture(params=["files", "mongo"])
def storage(request, monkeypatch, tmp_path):
    """Run a test on both workspace kinds. Returns the workspace_root to pass: a tmp folder for
    "files", None for "mongo" (MONGODB_URI is set and the store is an in-memory mongomock)."""
    if request.param == "files":
        return tmp_path / "workspace"
    import mongomock

    from services.agents import mongo_store

    monkeypatch.setenv("MONGODB_URI", "mongodb://mongomock")
    monkeypatch.setattr(mongo_store, "_STORE", mongo_store.MongoStore(mongomock.MongoClient().flamescout.files))
    return None


@pytest.fixture
def make_cache():
    """Write a prefetch cache for a building without any agent: make_cache(building_id, at=None)."""
    from services.agents import cache
    from services.agents.dossier import LAYERS, pull_layer

    def _make(building_id: str, at=None):
        ws = cache.cache_workspace(building_id)
        for name in LAYERS:
            ws.write(f"layers/{name}.json", pull_layer(name, building_id).model_dump_json())
        ws.write("notes/costar_summary.md", "cached costar note\n")
        return cache.stamp(building_id, at)

    return _make
