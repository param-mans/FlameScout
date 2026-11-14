"""Per-segment fact extraction.

The LLM's only job here is to turn ONE caller utterance into a list of
ExtractedFact. Merging, contradictions and open questions are deterministic
code in fusion.py, never the LLM.

- KeywordExtractor: regex stub. No API key needed. Used by tests and as the
  demo fallback. Good enough for the scripted S1-S4 calls.
- GeminiExtractor: TODO (person A) - same interface, using
  model.with_structured_output(ExtractionResult). Keep the prompt to
  "extract only what the caller literally said; omit anything not stated".
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel

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
