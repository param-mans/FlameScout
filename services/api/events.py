"""In-process pub/sub for /ws/incidents/{id}. publish() is safe from any thread."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict, deque
from typing import Optional

from flamescout_schemas import Event

log = logging.getLogger("flamescout.events")
HISTORY = 200


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._history: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=HISTORY))
        self._lock = threading.Lock()

    def subscribe(self, incident_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs[incident_id].add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, incident_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[incident_id] = {s for s in self._subs[incident_id] if s[1] is not q}

    def history(self, incident_id: str, since: int = 0) -> list[Event]:
        with self._lock:
            return list(self._history[incident_id])[-since:] if since else list(self._history[incident_id])

    def publish(self, incident_id: str, type_: str, payload: dict, trace_id: Optional[str] = None) -> Event:
        ev = Event(type=type_, incident_id=incident_id, trace_id=trace_id, payload=payload)  # type: ignore[arg-type]
        with self._lock:
            self._history[incident_id].append(ev)
            subs = list(self._subs[incident_id])
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, ev)
            except RuntimeError:          # loop closed: client is gone
                self.unsubscribe(incident_id, q)
        return ev

    def forget(self, incident_id: str) -> None:
        with self._lock:
            self._history.pop(incident_id, None)


def _offer(q: asyncio.Queue, ev: Event) -> None:
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        log.warning("ws queue full for %s; dropping %s", ev.incident_id, ev.type)
