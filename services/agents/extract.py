"""Per-segment fact extraction.

The LLM's only job here is to turn ONE caller utterance into a list of
ExtractedFact. Merging, contradictions and open questions are deterministic
code in fusion.py, never the LLM.

- KeywordExtractor: regex stub. No API key needed. Used by tests and as the
  demo fallback. Good enough for the scripted S1-S4 calls.
- GeminiExtractor: same interface, Gemini structured output. Its output is
  checked in code: unknown keys are dropped, the quote must appear verbatim in
  the utterance, values are coerced per key. Falls back to KeywordExtractor on
  any failure so a dead API never stalls fusion.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

log = logging.getLogger("flamescout.extract")

# Keys the fusion layer understands. Keep in sync with fusion.LIST_KEYS / QUESTIONS.
KEYS = [
    "location.address", "location.name", "incident.floor", "incident.visible",
    "people.reported", "people.trapped", "hazards.reported", "alarm.sounding",
    "access.notes",
]


class ExtractedFact(BaseModel):
    key: str
    value: Any
    quote: str  # the exact words that support it


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = []


class Extractor(Protocol):
    def extract(self, text: str) -> list[ExtractedFact]: ...


_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


class KeywordExtractor:
    ADDRESS = re.compile(
        r"\b(\d{2,6}\s+(?:[NSEW]\.?\s+)?[A-Z][\w']*(?:\s+[A-Z][\w']*)*\s+"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Way|Place|Pl))\b")
    NAME = re.compile(r"\b(?:from|at|of|near)\s+(?:the\s+)?([A-Z][\w']*(?:\s+[A-Z][\w']*)*\s+"
                      r"(?:Tower|Building|Plaza|Center|Centre|Lofts|Apartments))\b")
    FLOOR = re.compile(r"\b(" + "|".join(_ORDINALS) + r"|\d{1,2}(?:st|nd|rd|th))\s+floor\b", re.I)

    def extract(self, text: str) -> list[ExtractedFact]:
        out: list[ExtractedFact] = []
        if m := self.ADDRESS.search(text):
            out.append(ExtractedFact(key="location.address", value=m.group(1), quote=m.group(0)))
        if m := self.NAME.search(text):
            out.append(ExtractedFact(key="location.name", value=m.group(1), quote=m.group(0)))
        if m := self.FLOOR.search(text):
            word = m.group(1).lower()
            floor = _ORDINALS.get(word) or int(re.sub(r"\D", "", word))
            out.append(ExtractedFact(key="incident.floor", value=floor, quote=m.group(0)))
        low = text.lower()
        if "flame" in low or "fire's" in low or "on fire" in low:
            out.append(ExtractedFact(key="incident.visible", value="flames", quote=text))
        elif "smoke" in low:
            out.append(ExtractedFact(key="incident.visible", value="smoke", quote=text))
        if "trapped" in low or "can't get out" in low:
            out.append(ExtractedFact(key="people.trapped", value=True, quote=text))
        if any(w in low for w in ("people at the window", "someone's shouting", "someone is shouting",
                                  "people inside", "saw people")):
            out.append(ExtractedFact(key="people.reported", value=True, quote=text))
        for hz in ("gas", "chemical", "propane", "battery", "batteries"):
            if re.search(rf"\b{hz}\b", low):
                out.append(ExtractedFact(key="hazards.reported", value=hz, quote=text))
        if "alarm" in low:
            sounding = not re.search(r"\b(no|not|isn't|didn't)\b.*alarm|alarm.*\b(no|not|isn't|didn't)\b", low)
            out.append(ExtractedFact(key="alarm.sounding", value=sounding, quote=text))
        for note in ("back stairs", "rear stair", "side door", "back door", "fire escape"):
            if note in low:
                out.append(ExtractedFact(key="access.notes", value=note, quote=note))
        return out


# ---------------- Gemini ----------------

_BOOL_KEYS = {"people.reported", "people.trapped", "alarm.sounding"}
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}

_SYSTEM = (
    "You extract facts from ONE sentence spoken by a 911 caller about a fire. "
    "Extract only what the caller literally said; omit anything not stated. Never infer. "
    "Allowed keys and value formats:\n"
    "- location.address: street address as spoken\n"
    "- location.name: building or business name\n"
    "- incident.floor: floor number as an integer, e.g. 3\n"
    "- incident.visible: 'smoke' or 'flames'\n"
    "- people.reported: 'true' if the caller says people are inside/at windows\n"
    "- people.trapped: 'true' or 'false' if the caller says whether someone is trapped\n"
    "- hazards.reported: one hazard word per fact, e.g. 'gas', 'propane', 'batteries'\n"
    "- alarm.sounding: 'true' or 'false'\n"
    "- access.notes: an access detail, e.g. 'back stairs', 'side door locked'\n"
    "quote must be copied exactly, word for word, from the sentence."
)


class _LLMFact(BaseModel):
    key: str = Field(description="One of the allowed keys")
    value: str
    quote: str = Field(description="Exact words from the sentence that state this fact")


class _LLMResult(BaseModel):
    facts: list[_LLMFact] = []


def _coerce(key: str, value: str) -> Any:
    v = value.strip()
    if key == "incident.floor":
        m = re.search(r"\d+", v)
        if m:
            return int(m.group())
        return _ORDINALS.get(v.lower())
    if key in _BOOL_KEYS:
        return True if v.lower() in _TRUE else False if v.lower() in _FALSE else None
    if key == "incident.visible":
        return "flames" if "flame" in v.lower() or "fire" in v.lower() else "smoke" if "smoke" in v.lower() else None
    return v or None


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class GeminiExtractor:
    def __init__(self, fallback: Optional[Extractor] = None):
        self.fallback = fallback or KeywordExtractor()

    def extract(self, text: str) -> list[ExtractedFact]:
        from langchain_core.messages import HumanMessage, SystemMessage

        from .llm import structured
        res = structured("fast", _LLMResult, [SystemMessage(_SYSTEM), HumanMessage(text)])
        if res is None:
            return self.fallback.extract(text)
        return validate_llm_facts(text, res.facts)


def validate_llm_facts(text: str, facts: list[_LLMFact]) -> list[ExtractedFact]:
    """Deterministic gate on model output: known key, verbatim quote, coercible value."""
    out: list[ExtractedFact] = []
    hay = _squash(text)
    for f in facts:
        if f.key not in KEYS or not f.quote.strip() or _squash(f.quote) not in hay:
            log.info("dropped llm fact %s=%r (key/quote check)", f.key, f.value)
            continue
        value = _coerce(f.key, f.value)
        if value is None:
            continue
        out.append(ExtractedFact(key=f.key, value=value, quote=f.quote))
    return out


def get_extractor() -> Extractor:
    """EXTRACTOR=gemini|keyword. Default: gemini when configured, else keyword."""
    from .llm import gemini_configured
    choice = (os.getenv("EXTRACTOR") or "").lower()
    if choice == "keyword" or (choice != "gemini" and not gemini_configured("fast")):
        return KeywordExtractor()
    return GeminiExtractor()
