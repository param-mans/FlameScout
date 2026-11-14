import os

import pytest
from dotenv import load_dotenv

load_dotenv()  # GEMINI_* / LANGSMITH_* from .env, for the live tests


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
    """Unless a test is marked live: keyword extractor and no LangSmith uploads, whatever .env says.
    Every test gets an empty prefetch cache, so a real data/cache never changes a test's path."""
    from langsmith import utils

    from services.agents import cache

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "prefetch_cache")
    if "live" not in request.keywords:
        monkeypatch.setenv("EXTRACTOR", "keyword")
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
    utils.get_env_var.cache_clear()  # langsmith caches env lookups
    yield
    utils.get_env_var.cache_clear()


@pytest.fixture
def make_cache():
    """Write a prefetch cache for a building without any agent: make_cache(building_id, at=None)."""
    from services.agents import cache
    from services.agents.dossier import LAYERS, pull_layer

    def _make(building_id: str, at=None):
        folder = cache.cache_dir(building_id)
        for name in LAYERS:
            (folder / "layers").mkdir(parents=True, exist_ok=True)
            (folder / "layers" / f"{name}.json").write_text(pull_layer(name, building_id).model_dump_json())
        (folder / "notes").mkdir(exist_ok=True)
        (folder / "notes" / "costar_summary.md").write_text("cached costar note\n")
        return cache.stamp(building_id, at)

    return _make
