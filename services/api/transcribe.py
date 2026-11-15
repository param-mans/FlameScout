"""Call-in monologue: one device taps "Call in", the caller talks, taps Done, and the whole recording is
transcribed in one go. Every open instance of the web app follows the call (/ws/calls); nobody answers
and nothing is spoken back.

- transcribe_monologue(wav): tries LLM_PROVIDER, then LLM_FALLBACK_PROVIDER, on whichever has a key:
    gemini  GEMINI_FAST_MODEL, structured output {sentences}, one retry on schema error
    openai  audio transcriptions endpoint, OPENAI_TRANSCRIBE_MODEL (default whisper-1), OPENAI_BASE_URL
    xai     POST https://api.x.ai/v1/stt (multipart `file`; format=true needs `language`, STT_LANGUAGE,
            default en); response {text, language, duration, words}
  The openai/xai text is split into sentences in code. [] = no clear speech.
- timed(sentences, duration): deterministic timing for the transcript, spread over the recording by word
  count (the model's own timestamps would be guesses).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import wave
from typing import Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MAX_SECONDS = 300              # the browser stops recording at 5 min
MAX_WAV_BYTES = 12_000_000     # 5 min of 16 kHz mono PCM16 is 9.6 MB; Gemini takes up to 20 MB inline

PROMPT = ("Transcribe the speech in this audio verbatim, in the language spoken, as a list of sentences in "
          "the order they were said. Do not summarize, correct, translate or add anything. Write numbers as "
          "digits. Leave out silence, noise and filler that is not words. If there is no clear speech, return "
          "an empty list.\n"
          "The speaker is usually reporting a fire and gives a street address. Listen closely for it: write the "
          "house number as digits (\"ninety-seven hundred\" -> 9700, \"nine six six oh\" -> 9660), spell the "
          "street name and its suffix (Road, Lane, Drive, ...) as heard, and keep the whole address in one "
          "sentence. Never write an address that was not said.")
# Whisper's `prompt` is a style hint (it can be echoed on silent audio, so it holds no address of its own).
WHISPER_HINT = "Emergency fire call. The caller gives the street address, house number in digits, then the street name."


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


XAI_STT_URL = "https://api.x.ai/v1/stt"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Plain transcript -> sentences, split after . ! ? followed by whitespace."""
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def _gemini(wav: bytes) -> list[str]:
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
    raise RuntimeError(f"gemini transcription failed: {err!r}")


def _openai(wav: bytes) -> list[str]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=90, max_retries=1,
                    base_url=os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1")
    out = client.audio.transcriptions.create(model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
                                             file=("call.wav", wav, "audio/wav"), prompt=WHISPER_HINT)
    return split_sentences(out.text)


def _xai(wav: bytes) -> list[str]:
    import httpx

    # Option fields go before the file in the multipart body.
    data = {"format": "true", "language": os.getenv("STT_LANGUAGE", "en")}
    r = httpx.post(XAI_STT_URL, headers={"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"},
                   data=data, files={"file": ("call.wav", wav, "audio/wav")}, timeout=90)
    r.raise_for_status()
    return split_sentences(r.json().get("text") or "")


_BACKENDS = {"gemini": _gemini, "openai": _openai, "xai": _xai}


def providers() -> list[str]:
    """LLM_PROVIDER, then LLM_FALLBACK_PROVIDER, each only if its API key is set (gemini also needs
    GEMINI_FAST_MODEL)."""
    from services.agents import llm

    order = [llm.provider()]
    fb = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
    if fb in llm.PROVIDERS and fb not in order:
        order.append(fb)
    return [p for p in order
            if os.getenv(f"{p.upper()}_API_KEY") and (p != "gemini" or llm.configured("fast", "gemini"))]


def transcribe_monologue(wav: bytes) -> list[str]:
    """Raises when every provider fails (the endpoint answers 503); [] for no clear speech."""
    order = providers()
    if not order:
        raise RuntimeError("no transcription provider configured")
    err: Optional[Exception] = None
    for p in order:
        try:
            return _BACKENDS[p](wav)
        except Exception as e:  # noqa: BLE001 - quota, outage, bad key: try the next provider
            log.warning("transcription on %s failed: %r", p, e)
            err = e
    raise RuntimeError(f"transcription failed on {', '.join(order)}: {err!r}")


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
