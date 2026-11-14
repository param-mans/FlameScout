"""Gemini via langchain-google-genai. Model IDs come from env, never hard-coded.

    GEMINI_API_KEY, GEMINI_FAST_MODEL (extraction/copilot), GEMINI_VISION_MODEL (vision)

`structured()` = with_structured_output(schema) + retry on schema/validation
error. Returns None when Gemini is not configured or keeps failing, so every
caller has a deterministic fallback. LangSmith tracing is picked up from env
(LANGSMITH_TRACING / LANGSMITH_API_KEY) automatically by langchain.
"""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional, TypeVar

from pydantic import BaseModel

log = logging.getLogger("flamescout.llm")
T = TypeVar("T", bound=BaseModel)

_ENV = {"fast": "GEMINI_FAST_MODEL", "vision": "GEMINI_VISION_MODEL"}


def gemini_configured(kind: Literal["fast", "vision"] = "fast") -> bool:
    return bool(os.getenv("GEMINI_API_KEY") and os.getenv(_ENV[kind]))


def chat_model(kind: Literal["fast", "vision"]):
    if not gemini_configured(kind):
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=os.environ[_ENV[kind]],
                                  google_api_key=os.environ["GEMINI_API_KEY"],
                                  temperature=0, timeout=20, max_retries=1)


def structured(kind: Literal["fast", "vision"], schema: type[T], messages: list,
               retries: int = 2) -> Optional[T]:
    model = chat_model(kind)
    if model is None:
        return None
    runnable = model.with_structured_output(schema)
    for attempt in range(retries + 1):
        try:
            out = runnable.invoke(messages)
            return out if isinstance(out, schema) else schema.model_validate(out)
        except Exception as e:  # noqa: BLE001 - schema errors and transport errors both retry
            log.warning("gemini %s structured call failed (attempt %d): %s", kind, attempt + 1, e)
    return None
