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
def _offline_by_default(request, monkeypatch):
    """Unless a test is marked live: keyword extractor and no LangSmith uploads, whatever .env says."""
    from langsmith import utils

    if "live" not in request.keywords:
        monkeypatch.setenv("EXTRACTOR", "keyword")
        monkeypatch.setenv("LANGSMITH_TRACING", "false")
    utils.get_env_var.cache_clear()  # langsmith caches env lookups
    yield
    utils.get_env_var.cache_clear()
