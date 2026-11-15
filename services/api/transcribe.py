"""Call-in monologue: one device taps "Call in", the caller talks, taps Done, and the whole recording is
transcribed in one go. Every open instance of the web app follows the call (/ws/calls); nobody answers
and nothing is spoken back.

- transcribe_monologue(wav): Gemini (GEMINI_FAST_MODEL, always the gemini provider: the others take no
  audio), structured output {sentences}, one retry. [] = no clear speech.
- timed(sentences, duration): deterministic timing for the transcript, spread over the recording by word
  count (the model's own timestamps would be guesses).
"""
from __future__ import annotations

import base64
import io
import logging
import wave
from typing import Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MAX_SECONDS = 300              # the browser stops recording at 5 min
MAX_WAV_BYTES = 12_000_000     # 5 min of 16 kHz mono PCM16 is 9.6 MB; Gemini takes up to 20 MB inline

PROMPT = ("Transcribe the speech in this audio verbatim, in the language spoken, as a list of sentences in "
          "the order they were said. Do not summarize, correct, translate or add anything. Write numbers as "
          "digits. Leave out silence, noise and filler that is not words. If there is no clear speech, return "
          "an empty list.")


class Monologue(BaseModel):
    sentences: list[str] = Field(default_factory=list,
                                 description="What was said, verbatim, one sentence per item, in order")


_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        from services.agents.llm import make_chat

        _chain = make_chat("fast", timeout=90, max_retries=1, thinking="low",
                           prov="gemini").with_structured_output(Monologue)
    return _chain


def transcribe_monologue(wav: bytes) -> list[str]:
    """Raises on API failure (the endpoint answers 503); [] for no clear speech."""
    msg = [("system", PROMPT),
           ("human", [{"type": "text", "text": "Transcribe this call."},
                      {"type": "media", "mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}])]
    err: Optional[Exception] = None
    for _ in range(2):   # one retry on schema error
        try:
            out = _get_chain().invoke(msg)
        except Exception as e:  # noqa: BLE001 - parse error or API error: retry once
            err = e
            continue
        if out is not None:
            return [s.strip() for s in out.sentences if s.strip()]
    raise RuntimeError(f"transcription failed: {err!r}")


def duration_s(wav: bytes) -> float:
    """Length of a PCM WAV; ValueError if it isn't one."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError) as e:
        raise ValueError(f"not a PCM WAV: {e}") from e


def timed(sentences: list[str], duration: float) -> list[tuple[str, float, float]]:
    """[(sentence, t_start, t_end)] spread over the recording by word count, in order."""
    words = [max(1, len(s.split())) for s in sentences]
    total = sum(words) or 1
    out, t = [], 0.0
    for s, n in zip(sentences, words):
        end = t + duration * n / total
        out.append((s, round(t, 1), round(end, 1)))
        t = end
    return out
