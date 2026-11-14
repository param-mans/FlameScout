import os

import pytest

from services.agents.extract import KEYS, ExtractedFact, GeminiExtractor


class _NoFallback:
    def extract(self, text):
        pytest.fail("GeminiExtractor fell back instead of answering from Gemini")


def test_quote_check_ignores_case_whitespace_and_punctuation():
    text = "Fire's on the fourth floor, flames at the corner window."
    facts = [
        ExtractedFact(key="incident.floor", value=4, quote="Fires on  the FOURTH floor"),
        ExtractedFact(key="incident.visible", value="flames", quote="floor - flames"),
        ExtractedFact(key="people.trapped", value=True, quote="someone is trapped"),  # never said
        ExtractedFact(key="incident.visible", value="flames", quote="lames at the"),  # not whole words
        ExtractedFact(key="incident.floor", value=4, quote="..."),  # no words at all
        ExtractedFact(key="incident.room", value="corner", quote="corner window"),  # not in KEYS
    ]
    assert GeminiExtractor._grounded(facts, text) == facts[:2]


def test_timeout_comes_from_env(monkeypatch):
    monkeypatch.delenv("EXTRACT_TIMEOUT_S", raising=False)
    assert GeminiExtractor().timeout_s == 4.0
    monkeypatch.setenv("EXTRACT_TIMEOUT_S", "2.5")
    assert GeminiExtractor().timeout_s == 2.5
    monkeypatch.setenv("EXTRACT_TIMEOUT_S", "fast")
    assert GeminiExtractor().timeout_s == 4.0


@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_FAST_MODEL")),
                    reason="GEMINI_API_KEY / GEMINI_FAST_MODEL not set")
def test_gemini_extracts_only_known_keys_with_exact_quotes():
    text = "Fire's on the fourth floor, flames at the corner window."
    # Generous timeout: this checks the Gemini path, not the latency fallback.
    facts = GeminiExtractor(timeout_s=20, fallback=_NoFallback()).extract(text)
    assert {f.key: f.value for f in facts}["incident.floor"] == 4
    assert all(f.key in KEYS for f in facts)
