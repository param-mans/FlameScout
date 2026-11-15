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


@pytest.mark.parametrize("text,value", [
    ("There is a fire at 9660 fairdale lane.", "9660 fairdale lane"),        # lowercase transcript
    ("nine six six oh Fairdale Ln", "nine six six oh Fairdale Ln"),         # number said as words
    ("it's 9700, Tanner Road", "9700 Tanner Road"),
    ("1200 N. Main Street", "1200 N. Main Street"),
    ("I have 2 kids on Fairdale Lane", "Fairdale Lane"),                    # 2 is not a house number
    ("we are on Tanner Road please hurry", "Tanner Road"),
])
def test_spot_address_finds_what_was_said(text, value):
    from services.agents.extract import spot_address

    f = spot_address(text)
    assert f is not None and f.value == value
    assert GeminiExtractor._grounded([f], text) == [f]                     # the quote is verbatim


@pytest.mark.parametrize("text", ["I'm not on your domain.", "I have two kids upstairs", "on my way",
                                  "third floor of the Wilshire Tower", "the fire is at the back door", "Main St"])
def test_spot_address_never_guesses(text):
    from services.agents.extract import spot_address

    assert spot_address(text) is None


def test_llm_extractor_gets_the_address_it_missed():
    text = "Smoke everywhere, we're at 9700 tanner road"
    smoke = ExtractedFact(key="incident.visible", value="smoke", quote="Smoke everywhere")
    facts = GeminiExtractor._with_address([smoke], text)
    assert [f.key for f in facts] == ["incident.visible", "location.address"]
    said = ExtractedFact(key="location.address", value="9700 Tanner Road", quote="9700 tanner road")
    assert GeminiExtractor._with_address([said], text) == [said]           # the model's own wins
