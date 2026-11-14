"""Chat models for every LLM call: Gemini (langchain-google-genai) or OpenAI (langchain-openai).
Model IDs come from env, never hard-coded.

    LLM_PROVIDER=gemini (default)  GEMINI_API_KEY, GEMINI_FAST_MODEL (extraction/copilot/agents),
                                   GEMINI_VISION_MODEL (vision)
    LLM_PROVIDER=openai            OPENAI_API_KEY, OPENAI_FAST_MODEL, OPENAI_VISION_MODEL,
                                   OPENAI_REASONING_EFFORT (optional; only for reasoning models)

`structured()` = with_structured_output(schema) + retry on schema/validation
error. Returns None when the model is not configured or keeps failing, so every
caller has a deterministic fallback. LangSmith tracing is picked up from env
(LANGSMITH_TRACING / LANGSMITH_API_KEY) automatically by langchain.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal, Optional, TypeVar

from pydantic import BaseModel

log = logging.getLogger("flamescout.llm")
T = TypeVar("T", bound=BaseModel)
Kind = Literal["fast", "vision"]
Thinking = Literal["low", "none", "default"]


def provider() -> str:
    p = os.getenv("LLM_PROVIDER", "gemini").lower()
    if p not in ("gemini", "openai"):
        raise ValueError(f"LLM_PROVIDER must be gemini or openai, not {p!r}")
    return p


def _env(kind: Kind) -> tuple[str, str]:
    """(API key variable, model ID variable) for the current provider."""
    prefix = provider().upper()
    return f"{prefix}_API_KEY", f"{prefix}_{kind.upper()}_MODEL"


def configured(kind: Kind = "fast") -> bool:
    key, model = _env(kind)
    return bool(os.getenv(key) and os.getenv(model))


gemini_configured = configured  # old name, kept for callers


def model_name(kind: Kind = "fast") -> Optional[str]:
    return os.getenv(_env(kind)[1])


def make_chat(kind: Kind = "fast", *, timeout: float = 20, max_retries: int = 1,
              thinking: Thinking = "default", temperature: Optional[float] = None) -> Any:
    """A LangChain chat model for the configured provider. Raises KeyError when it is not configured.

    thinking: Gemini's thinking level ("low" = thinking_level low, "none" = thinking_budget 0).
    OpenAI ignores it and uses OPENAI_REASONING_EFFORT when set (reasoning models only).
    """
    key, model = _env(kind)
    extra: dict[str, Any] = {} if temperature is None else {"temperature": temperature}
    if provider() == "openai":
        from langchain_openai import ChatOpenAI

        if effort := os.getenv("OPENAI_REASONING_EFFORT"):
            extra["reasoning_effort"] = effort
        return ChatOpenAI(model=os.environ[model], api_key=os.environ[key],
                          timeout=timeout, max_retries=max_retries, **extra)

    from langchain_google_genai import ChatGoogleGenerativeAI

    if thinking == "low":
        extra["thinking_level"] = "low"
    elif thinking == "none":
        extra["thinking_budget"] = 0
    return ChatGoogleGenerativeAI(model=os.environ[model], google_api_key=os.environ[key],
                                  timeout=timeout, max_retries=max_retries, **extra)


def chat_model(kind: Kind):
    if not configured(kind):
        return None
    return make_chat(kind, timeout=20, max_retries=1,
                     temperature=None if os.getenv("OPENAI_REASONING_EFFORT") and provider() == "openai" else 0)


def structured(kind: Kind, schema: type[T], messages: list,
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
            log.warning("%s %s structured call failed (attempt %d): %s", provider(), kind, attempt + 1, e)
    return None
