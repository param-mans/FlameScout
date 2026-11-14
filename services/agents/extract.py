"""Per-segment fact extraction.

The LLM's only job here is to turn ONE caller utterance into a list of
ExtractedFact. Merging, contradictions and open questions are deterministic
code in fusion.py, never the LLM.

- KeywordExtractor: regex stub. No API key needed. Used by tests and as the
  demo fallback. Good enough for the scripted S1-S4 calls.
- GeminiExtractor: same interface, using
  model.with_structured_output(ExtractionResult). Never raises: any error, or
  a call slower than EXTRACT_TIMEOUT_S (default 4 s), falls back to
  KeywordExtractor for that utterance.
- get_extractor(): picks one from EXTRACTOR=keyword|gemini (default keyword).
- validate_llm_facts(): the same checks for raw model facts (_LLMFact) from
  other callers: known key, verbatim quote, value coerced per key.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

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


PROMPT = f"""You extract facts from ONE utterance spoken by a caller on a fire emergency call.

- Extract only what the caller literally said. Omit anything not stated; never infer or guess.
- Use only these keys: {", ".join(KEYS)}. Never use any other key.
- For every fact include the exact quote: the caller's words that support it, copied verbatim
  from the utterance.
- If the utterance states none of these, return an empty facts list.

Values:
- location.address: the street address as said. location.name: the building name as said.
- incident.floor: the floor number as an integer ("third floor" -> 3).
- incident.visible: "smoke" or "flames".
- people.reported, people.trapped, alarm.sounding: true or false.
- hazards.reported: one fact per hazard, a single lowercase word ("gas", "propane").
- access.notes: one fact per note, a short phrase as said ("back stairs")."""


def _squash(s: str) -> str:
    """Lowercase words only: no punctuation, single spaces, padded so matches are whole-word."""
    s = re.sub(r"['\u2018\u2019]", "", s.lower())  # someone's == someones
    return f" {' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())} "


def _timeout_from_env() -> float:
    raw = os.environ.get("EXTRACT_TIMEOUT_S", "")
    try:
        return float(raw) if raw.strip() else GeminiExtractor.TIMEOUT_S
    except ValueError:
        log.warning("Bad EXTRACT_TIMEOUT_S=%r, using %s", raw, GeminiExtractor.TIMEOUT_S)
        return GeminiExtractor.TIMEOUT_S


class GeminiExtractor:
    TIMEOUT_S = 4.0  # default; override with EXTRACT_TIMEOUT_S
    MIN_API_DEADLINE_S = 10.0  # the API rejects shorter request deadlines with a 400

    def __init__(self, timeout_s: Optional[float] = None, fallback: Optional[Extractor] = None):
        self.timeout_s = _timeout_from_env() if timeout_s is None else timeout_s
        self.fallback = fallback or KeywordExtractor()
        self._chain = None
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini-extract")

    def extract(self, text: str) -> list[ExtractedFact]:
        fut = None
        try:
            # Wall-clock cap enforced here; the client's own (longer) deadline only
            # reaps the abandoned thread.
            fut = self._pool.submit(self._ask, self._get_chain(), text)
            facts = fut.result(timeout=self.timeout_s)
        except Exception as e:  # incl. TimeoutError, missing env, quota, schema error
            if fut is not None:
                fut.cancel()
            log.warning("GeminiExtractor fell back to keywords: %r", e)
            return self.fallback.extract(text)
        return self._grounded(facts, text)

    def _get_chain(self):
        if self._chain is None:
            from .llm import make_chat

            llm = make_chat(
                "fast",
                timeout=max(self.timeout_s, self.MIN_API_DEADLINE_S),
                max_retries=1,  # = attempts; a network retry can't fit in the budget
                thinking="low",  # Gemini: default thinking takes ~4.5 s per utterance
            )
            self._chain = llm.with_structured_output(ExtractionResult, include_raw=True)
        return self._chain

    def _ask(self, chain, text: str) -> list[ExtractedFact]:
        out: dict = {}
        for _ in range(2):  # one retry on schema error
            out = chain.invoke([("system", PROMPT), ("human", text)])
            if out["parsed"] is not None:
                return out["parsed"].facts
        # content is a list of blocks on this model; .text is the plain string
        raise ValueError(f"schema error: {out['raw'].text[:200]!r}")

    @staticmethod
    def _grounded(facts: list[ExtractedFact], text: str) -> list[ExtractedFact]:
        """Drop anything the prompt forbids: unknown keys, quotes the caller didn't say."""
        said = _squash(text)
        kept = []
        for f in facts:
            if f.key in KEYS and _squash(f.quote).strip() and _squash(f.quote) in said:
                kept.append(f)
            else:
                log.warning("GeminiExtractor dropped ungrounded fact: %r", f)
        return kept


def get_extractor() -> Extractor:
    """EXTRACTOR=keyword|gemini|llm (default keyword). gemini and llm both mean the LLM extractor on
    LLM_PROVIDER's model (the class keeps its old name)."""
    name = os.environ.get("EXTRACTOR", "keyword").strip().lower()
    if name in ("gemini", "llm"):
        return GeminiExtractor()
    if name != "keyword":
        log.warning("Unknown EXTRACTOR=%r, using keyword", name)
    return KeywordExtractor()


# ---------- raw model facts from other callers (services/api) ----------

_BOOL_KEYS = {"people.reported", "people.trapped", "alarm.sounding"}
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}

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
