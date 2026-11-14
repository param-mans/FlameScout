"""Model-call stalls: a client timeout, one retry, and a log line for each.

Single Gemini calls occasionally sat for 20-45 s with no error and nothing in the
logs. Every orchestrator and subagent model is now built with MODEL_TIMEOUT_S (the
API refuses deadlines under 10 s) and no SDK retries; StallRetryMiddleware is the
one place that retries, once, and says which agent stalled.

Run deadline: every orchestrator run has a wall-clock cap (orchestrator.run_deadline_s). When it
passes, the run's cancel event is set; CancelMiddleware, on every agent of that run, raises
RunCancelled at the next model or tool call, and neither the retry nor the failover start another
attempt. The event travels in a ContextVar, so the parallel subagents (tool threads) see it too.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

from langchain.agents.middleware import AgentMiddleware
from langgraph.errors import GraphBubbleUp

log = logging.getLogger(__name__)

MODEL_TIMEOUT_S = 12.0


class RunCancelled(Exception):
    """The run's deadline passed: the agent stops at its next model or tool call."""


RUN_CANCEL: ContextVar[Optional[threading.Event]] = ContextVar("flamescout_run_cancel", default=None)


def cancelled() -> bool:
    """True once the current run's deadline has passed (always False outside a run)."""
    event = RUN_CANCEL.get()
    return event is not None and event.is_set()


def stop_if_cancelled(where: str) -> None:
    if cancelled():
        raise RunCancelled(f"run deadline reached: {where} not started")
_STALL = re.compile(r"\b504 [A-Z_]+|DEADLINE_EXCEEDED")           # "504 DEADLINE_EXCEEDED. {...}"
_TRANSIENT = re.compile(r"\b(429|500|503) [A-Z_]+")               # "429 RESOURCE_EXHAUSTED. {...}"


@dataclass
class StallCounter:
    """Shared by every agent of one incident; the layer subagents run concurrently."""
    stalls: int = 0    # model calls that hit the timeout
    retries: int = 0   # retries made (after a stall or a transient API error)
    by_agent: dict[str, int] = field(default_factory=dict)  # agent name -> stalls
    failovers: int = 0  # calls answered (or attempted) on the backup provider
    failovers_by_agent: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def stalled(self, agent: str) -> None:
        with self._lock:
            self.stalls += 1
            self.by_agent[agent] = self.by_agent.get(agent, 0) + 1

    def failed_over(self, agent: str) -> None:
        with self._lock:
            self.failovers += 1
            self.failovers_by_agent[agent] = self.failovers_by_agent.get(agent, 0) + 1

    def retried(self) -> None:
        with self._lock:
            self.retries += 1

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.stalls, self.retries


def classify(error: BaseException) -> Optional[str]:
    """"stall" for a timeout, "transient" for an API error worth one retry, None for anything else."""
    seen: set[int] = set()
    e: Optional[BaseException] = error
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        text = str(e)
        if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__ or _STALL.search(text):
            return "stall"
        if _TRANSIENT.search(text):
            return "transient"
        e = e.__cause__ or e.__context__
    return None


class CancelMiddleware(AgentMiddleware):
    """Outermost on every agent: after the run deadline, no new model or tool call starts."""

    def __init__(self, agent: str):
        super().__init__()
        self.agent = agent

    def wrap_model_call(self, request, handler):
        stop_if_cancelled(f"{self.agent} model call")
        return handler(request)

    def wrap_tool_call(self, request, handler):
        stop_if_cancelled(f"{self.agent} tool call {request.tool_call.get('name')}")
        return handler(request)


class StallRetryMiddleware(AgentMiddleware):
    """Retry a model call once when it times out (or fails transiently), and log it with the agent's name."""

    def __init__(self, agent: str, counter: StallCounter):
        super().__init__()
        self.agent, self.counter = agent, counter

    def wrap_model_call(self, request, handler):
        t0 = time.perf_counter()
        try:
            return handler(request)
        except Exception as first:
            kind = classify(first)
            if kind is None or cancelled():  # past the deadline: no second attempt
                raise
            waited = time.perf_counter() - t0
            if kind == "stall":
                self.counter.stalled(self.agent)
                log.warning("Model stall: %s got no answer in %.1f s; retrying once", self.agent, waited)
            else:
                log.warning("Model error: %s call failed after %.1f s (%s); retrying once",
                            self.agent, waited, str(first)[:80])
        self.counter.retried()
        t1 = time.perf_counter()
        try:
            response = handler(request)
        except Exception as second:
            if classify(second) == "stall":
                self.counter.stalled(self.agent)
                log.error("Model stall: %s stalled again on the retry (%.1f s); giving up",
                          self.agent, time.perf_counter() - t1)
            else:
                log.error("Model retry: %s failed on the retry too (%s)", self.agent, str(second)[:80])
            raise
        log.warning("Model retry: %s answered on the retry in %.1f s", self.agent, time.perf_counter() - t1)
        return response


class FailoverMiddleware(AgentMiddleware):
    """When a model call fails, ask the same question once on the other provider (LLM_FALLBACK_PROVIDER).

    Sits inside StallRetryMiddleware, so a failed primary goes straight to the backup model instead of
    waiting out a retry on the provider that just failed. Interrupts (GraphBubbleUp) pass through.
    A permanent failure (unknown model, bad key) opens llm.BREAKER: for BREAKER_S every agent skips
    that model and asks the backup directly.
    """

    def __init__(self, agent: str, fallback, counter: StallCounter):
        super().__init__()
        self.agent, self.fallback, self.counter = agent, fallback, counter

    def wrap_model_call(self, request, handler):
        from .llm import BREAKER, is_permanent

        primary = getattr(request, "model", None)
        if BREAKER.is_open(primary):
            self.counter.failed_over(self.agent)
            log.info("Model failover: %s skips %s (breaker open)", self.agent, getattr(primary, "model", primary))
            return handler(request.override(model=self.fallback))
        try:
            return handler(request)
        except (GraphBubbleUp, RunCancelled):
            raise
        except Exception as first:
            if cancelled():  # past the deadline: do not start a call on the backup
                raise
            self.counter.failed_over(self.agent)
            if is_permanent(first):
                BREAKER.trip(primary, first)
            log.warning("Model failover: %s failed (%s: %s); asking the backup model",
                        self.agent, classify(first) or ("permanent" if is_permanent(first) else "error"),
                        str(first)[:80])
        t0 = time.perf_counter()
        response = handler(request.override(model=self.fallback))
        log.warning("Model failover: %s answered on the backup model in %.1f s", self.agent, time.perf_counter() - t0)
        return response
