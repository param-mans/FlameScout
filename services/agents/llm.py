"""Chat models for every LLM call: Gemini (langchain-google-genai), OpenAI (langchain-openai) or
xAI Grok (langchain-xai).
Model IDs come from env, never hard-coded.

    LLM_PROVIDER=gemini (default)  GEMINI_API_KEY, GEMINI_FAST_MODEL (extraction/copilot/agents),
                                   GEMINI_VISION_MODEL (vision)
    LLM_PROVIDER=openai            OPENAI_API_KEY, OPENAI_FAST_MODEL, OPENAI_VISION_MODEL,
                                   OPENAI_REASONING_EFFORT (optional; only for reasoning models),
                                   OPENAI_BASE_URL (optional; any OpenAI-compatible endpoint)
    LLM_PROVIDER=xai               XAI_API_KEY, XAI_FAST_MODEL, XAI_VISION_MODEL

Two providers at once (both optional, each needs its own key + model IDs above):
    LLM_FALLBACK_PROVIDER=xai      failover: a call that fails on LLM_PROVIDER (quota, outage, timeout,
                                   schema error) is retried once on this provider.
    CRITIC_PROVIDER=xai            the orchestrator's critic runs on this provider, so the brief is checked
                                   by a different model than the one that drafted it (default: LLM_PROVIDER).

`structured()` = with_structured_output(schema) + retry on schema/validation
error. Returns None when the model is not configured or keeps failing, so every
caller has a deterministic fallback. LangSmith tracing is picked up from env
(LANGSMITH_TRACING / LANGSMITH_API_KEY) automatically by langchain.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Literal, Optional, TypeVar

from pydantic import BaseModel

log = logging.getLogger("flamescout.llm")
T = TypeVar("T", bound=BaseModel)
Kind = Literal["fast", "vision"]
Thinking = Literal["low", "none", "default"]


PROVIDERS = ("gemini", "openai", "xai")

# ---------- circuit breaker ----------

BREAKER_S = 300.0  # how long a model that failed for good is skipped before it is tried again
_PERMANENT = re.compile(r"\b(401|403|404)\b|NOT_FOUND|PERMISSION_DENIED|UNAUTHENTICATED|API key not valid|"
                        r"invalid_api_key|model_not_found|does not exist", re.I)


def is_permanent(error: BaseException) -> bool:
    """A failure a retry will not fix: unknown model, bad or missing key, no access. Not quota (429)."""
    seen: set[int] = set()
    e: Optional[BaseException] = error
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if _PERMANENT.search(f"{type(e).__name__} {e}"):
            return True
        e = e.__cause__ or e.__context__
    return False


def model_key(model: Any) -> Optional[str]:
    """"<class>:<model id>" for a chat model or a "<provider>:<model id>" string; None if unknown."""
    if isinstance(model, str):
        return model or None
    mid = getattr(model, "model", None) or getattr(model, "model_name", None)
    return f"{type(model).__name__}:{str(mid).removeprefix('models/')}" if isinstance(mid, str) else None


class Breaker:
    """Process-wide list of models to skip for BREAKER_S after a permanent failure, so every later call
    goes straight to the backup instead of paying the failing round trip (or a 12 s timeout) first."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_open(self, model: Any) -> bool:
        key = model_key(model)
        with self._lock:
            return key is not None and self._until.get(key, 0.0) > time.monotonic()

    def trip(self, model: Any, error: BaseException) -> None:
        if (key := model_key(model)) is None:
            return
        with self._lock:
            fresh = self._until.get(key, 0.0) <= time.monotonic()
            self._until[key] = time.monotonic() + BREAKER_S
        if fresh:
            log.error("Breaker open: %s failed for good (%s); using the backup for %.0f s",
                      key, str(error)[:120], BREAKER_S)

    def reset(self) -> None:
        with self._lock:
            self._until.clear()


BREAKER = Breaker()


def _check(var: str, p: str) -> str:
    if p not in PROVIDERS:
        raise ValueError(f"{var} must be one of {', '.join(PROVIDERS)}, not {p!r}")
    return p


def provider() -> str:
    return _check("LLM_PROVIDER", os.getenv("LLM_PROVIDER", "gemini").strip().lower() or "gemini")


def fallback_provider(kind: Kind = "fast") -> Optional[str]:
    """LLM_FALLBACK_PROVIDER when it is set, differs from LLM_PROVIDER and is configured for `kind`."""
    p = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
    if not p:
        return None
    _check("LLM_FALLBACK_PROVIDER", p)
    if p == provider():
        return None
    if not configured(kind, p):
        log.warning("LLM_FALLBACK_PROVIDER=%s is missing its API key or %s model ID; no failover", p, kind)
        return None
    return p


def critic_provider() -> str:
    """CRITIC_PROVIDER when it is set and configured, else LLM_PROVIDER."""
    p = os.getenv("CRITIC_PROVIDER", "").strip().lower()
    if not p:
        return provider()
    _check("CRITIC_PROVIDER", p)
    if not configured("fast", p):
        log.warning("CRITIC_PROVIDER=%s is missing its API key or fast model ID; critic uses %s", p, provider())
        return provider()
    return p


def _env(kind: Kind, prov: Optional[str] = None) -> tuple[str, str]:
    """(API key variable, model ID variable) for a provider (default: LLM_PROVIDER)."""
    prefix = (prov or provider()).upper()
    return f"{prefix}_API_KEY", f"{prefix}_{kind.upper()}_MODEL"


def configured(kind: Kind = "fast", prov: Optional[str] = None) -> bool:
    key, model = _env(kind, prov)
    return bool(os.getenv(key) and os.getenv(model))


gemini_configured = configured  # old name, kept for callers


def model_name(kind: Kind = "fast", prov: Optional[str] = None) -> Optional[str]:
    return os.getenv(_env(kind, prov)[1])


def make_chat(kind: Kind = "fast", *, timeout: float = 20, max_retries: int = 1,
              thinking: Thinking = "default", temperature: Optional[float] = None,
              prov: Optional[str] = None) -> Any:
    """A LangChain chat model for a provider (default: LLM_PROVIDER). Raises KeyError when it is not
    configured.

    thinking: Gemini's thinking level ("low" = thinking_level low, "none" = thinking_budget 0).
    OpenAI ignores it and uses OPENAI_REASONING_EFFORT when set (reasoning models only); xAI ignores it.
    """
    prov = prov or provider()
    key, model = _env(kind, prov)
    extra: dict[str, Any] = {} if temperature is None else {"temperature": temperature}
    if prov == "xai":
        from langchain_xai import ChatXAI

        return ChatXAI(model=os.environ[model], api_key=os.environ[key],
                       timeout=timeout, max_retries=max_retries, **extra)
    if prov == "openai":
        from langchain_openai import ChatOpenAI

        if effort := os.getenv("OPENAI_REASONING_EFFORT"):
            extra["reasoning_effort"] = effort
        # An empty OPENAI_BASE_URL (copied from .env.example) would become the client's base URL.
        extra["base_url"] = os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
        return ChatOpenAI(model=os.environ[model], api_key=os.environ[key],
                          timeout=timeout, max_retries=max_retries, **extra)

    from langchain_google_genai import ChatGoogleGenerativeAI

    if thinking == "low":
        extra["thinking_level"] = "low"
    elif thinking == "none":
        extra["thinking_budget"] = 0
    return ChatGoogleGenerativeAI(model=os.environ[model], google_api_key=os.environ[key],
                                  timeout=timeout, max_retries=max_retries, **extra)


def make_fallback_chat(kind: Kind = "fast", **kw: Any) -> Optional[Any]:
    """The same model on LLM_FALLBACK_PROVIDER, or None when no failover is configured."""
    p = fallback_provider(kind)
    return make_chat(kind, prov=p, **kw) if p else None


def chat_providers(kind: Kind) -> list[str]:
    """Providers to try for a plain call, in order: LLM_PROVIDER, then the failover provider."""
    return [p for p in (provider(), fallback_provider(kind)) if p and configured(kind, p)]


def chat_model(kind: Kind, prov: Optional[str] = None):
    prov = prov or provider()
    if not configured(kind, prov):
        return None
    return make_chat(kind, timeout=20, max_retries=1, prov=prov,
                     temperature=None if os.getenv("OPENAI_REASONING_EFFORT") and prov == "openai" else 0)


def structured(kind: Kind, schema: type[T], messages: list,
               retries: int = 2) -> Optional[T]:
    """Retries on LLM_PROVIDER, then (if set) the same number of tries on LLM_FALLBACK_PROVIDER.
    A provider whose model failed for good (BREAKER) is skipped while a backup is left to try."""
    provs = chat_providers(kind)
    for i, prov in enumerate(provs):
        key = f"{prov}:{model_name(kind, prov)}"
        if BREAKER.is_open(key) and i < len(provs) - 1:
            continue
        runnable = chat_model(kind, prov).with_structured_output(schema)
        for attempt in range(retries + 1):
            try:
                out = runnable.invoke(messages)
                return out if isinstance(out, schema) else schema.model_validate(out)
            except Exception as e:  # noqa: BLE001 - schema errors and transport errors both retry
                log.warning("%s %s structured call failed (attempt %d): %s", prov, kind, attempt + 1, e)
                if is_permanent(e):
                    BREAKER.trip(key, e)
                    break
    return None
