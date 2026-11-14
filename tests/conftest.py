import json
from pathlib import Path

import pytest

from flamescout_schemas import CallSegment

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "fixtures"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests never call Gemini, geocoders or Tiger, even if .env has keys."""
    monkeypatch.setenv("EXTRACTOR", "keyword")
    for k in ("GEMINI_API_KEY", "TIGER_DATABASE_URL", "GEOCODER"):
        monkeypatch.delenv(k, raising=False)


def load_script(script_id: str) -> list[CallSegment]:
    data = json.loads((FIXTURES / "calls" / f"{script_id.lower()}.json").read_text())
    return [CallSegment(**s) for s in data["segments"]]
