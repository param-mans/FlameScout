"""Live calls from the web app's Call in: call -> (incident, c1/c2/...). Thread-safe, in memory.

A new call joins the incident that had a call in the last `window_s` seconds (a second caller about
the same fire becomes c2, so the callers' facts can contradict), else a new incident.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("flamescout.calls")

ENDED = {"completed", "canceled", "failed"}


@dataclass
class LiveCall:
    incident_id: str
    call_id: str                       # c1, c2, ... within the incident (CallSegment.call_id)
    direction: str = "callin"
    status: str = "started"            # started | connected | transcribing | completed | canceled | failed
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.status not in ENDED

    def public(self) -> dict[str, Any]:
        return {"incident_id": self.incident_id, "call_id": self.call_id, "direction": self.direction,
                "status": self.status, "started_at": self.started_at, "ended_at": self.ended_at}


class CallRegistry:
    def __init__(self, create_incident: Callable[[], str], exists: Callable[[str], bool],
                 window_s: float = 600.0, clock: Callable[[], float] = time.time) -> None:
        self._create, self._exists, self.window_s, self._clock = create_incident, exists, window_s, clock
        self._calls: list[LiveCall] = []
        self._lock = threading.Lock()

    def calls(self, active_only: bool = False) -> list[LiveCall]:
        with self._lock:
            return [c for c in self._calls if c.active or not active_only]

    def start(self, direction: str = "callin") -> LiveCall:
        """A new call, in the incident of a recent call or a new incident."""
        with self._lock:
            incident_id = self._pick_incident()
            n = sum(1 for c in self._calls if c.incident_id == incident_id)
            call = LiveCall(incident_id=incident_id, call_id=f"c{n + 1}", direction=direction,
                            started_at=self._clock())
            self._calls.append(call)
        log.info("call %s/%s started", call.incident_id, call.call_id)
        return call

    def get(self, incident_id: str, call_id: str) -> Optional[LiveCall]:
        with self._lock:
            return next((c for c in reversed(self._calls)
                         if c.incident_id == incident_id and c.call_id == call_id), None)

    def end(self, call: LiveCall, status: str = "completed") -> None:
        with self._lock:
            if call.active:
                call.status = status
            if call.ended_at is None:
                call.ended_at = self._clock()

    def _pick_incident(self) -> str:
        """Caller holds the lock."""
        now = self._clock()
        for c in reversed(self._calls):
            last = c.ended_at if c.ended_at is not None else now
            if (c.active or now - last <= self.window_s) and self._exists(c.incident_id):
                return c.incident_id
        return self._create()
