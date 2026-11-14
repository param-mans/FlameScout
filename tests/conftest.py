import os

import pytest
from dotenv import load_dotenv

load_dotenv()  # GEMINI_* from .env, for the live tests


def pytest_collection_modifyitems(config, items):
    """@pytest.mark.live tests call real APIs: opt in with RUN_LIVE=1."""
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _keyword_extractor(monkeypatch):
    """Fusion tests stay offline and deterministic whatever EXTRACTOR says in .env."""
    monkeypatch.setenv("EXTRACTOR", "keyword")
