"""Model-call stalls: a client timeout, one retry, and a log line for each.

Single Gemini calls occasionally sat for 20-45 s with no error and nothing in the
logs. Every orchestrator and subagent model is now built with MODEL_TIMEOUT_S (the
API refuses deadlines under 10 s) and no SDK retries; StallRetryMiddleware is the
one place that retries, once, and says which agent stalled.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger(__name__)

MODEL_TIMEOUT_S = 12.0
_STALL = re.compile(r"\b504 [A-Z_]+|DEADLINE_EXCEEDED")           # "504 DEADLINE_EXCEEDED. {...}"
_TRANSIENT = re.compile(r"\b(429|500|503) [A-Z_]+")               # "429 RESOURCE_EXHAUSTED. {...}"


@dataclass
class StallCounter:
    """Shared by every agent of one incident; the layer subagents run concurrently."""
    stalls: int = 0    # model calls that hit the timeout
    retries: int = 0   # retries made (after a stall or a transient API error)
    by_agent: dict[str, int] = field(default_factory=dict)  # agent name -> stalls
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def stalled(self, agent: str) -> None:
        with self._lock:
            self.stalls += 1
            self.by_agent[agent] = self.by_agent.get(agent, 0) + 1

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
            if kind is None:
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
