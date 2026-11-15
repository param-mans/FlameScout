"""Caller cameras: the device that starts a call also streams its phone camera into the incident.

Each camera is its own call in the incident (cam1, cam2, ...), so what it sees can corroborate or contradict
what callers say, like a third caller. Frames are read one at a time (services/agents/camera.py); a frame
that arrives while the previous one is still being read is refused (409) and the device sends the next one.
Two ways in: the device that starts a call opens its own camera (start), or the dispatcher texts the caller
a link (invite): the camera call exists at once as "invited", and the caller's phone opens it with the
link's secret token (by_token) and streams like any other.
Thread-safe, in memory; the segments themselves are in the incident's replay log like any other.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.agents.camera import CameraState

log = logging.getLogger("flamescout.cameras")

MAX_FRAME_BYTES = 3_000_000


@dataclass
class Camera:
    incident_id: str
    call_id: str                        # cam1, cam2, ... (CallSegment.call_id)
    status: str = "streaming"           # invited (link sent, phone not open yet) | streaming | ended
    token: Optional[str] = None         # invite link secret; never in public()
    sent_to: Optional[str] = None       # masked phone number the link was texted to
    invite: Optional[dict] = None       # the texted link: {state: sent|waiting|failed|not_configured, detail, line}
    started_at: float = 0.0
    ended_at: Optional[float] = None
    frames: int = 0                     # frames read
    observations: int = 0               # frames that added something (became a segment)
    state: CameraState = field(default_factory=CameraState)
    busy: threading.Lock = field(default_factory=threading.Lock)

    @property
    def active(self) -> bool:
        return self.status != "ended"

    def public(self) -> dict[str, Any]:
        s = self.state
        return {"incident_id": self.incident_id, "call_id": self.call_id, "status": self.status,
                "started_at": self.started_at, "ended_at": self.ended_at, "frames": self.frames,
                "observations": self.observations, "sent_to": self.sent_to, "invite": self.invite,
                "seen": {"room": s.room, "fire": s.fire, "hazards": sorted(s.hazards), "people": s.people}}


class CameraRegistry:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._cams: list[Camera] = []
        self._lock = threading.Lock()

    def start(self, incident_id: str, invite: bool = False) -> Camera:
        """A new camera call. invite=True: waiting for the caller to open the texted link (by its token)."""
        with self._lock:
            n = sum(1 for c in self._cams if c.incident_id == incident_id)
            cam = Camera(incident_id=incident_id, call_id=f"cam{n + 1}", started_at=self._clock())
            if invite:
                cam.status, cam.token = "invited", secrets.token_urlsafe(16)
            self._cams.append(cam)
        log.info("camera %s/%s %s", incident_id, cam.call_id, cam.status)
        return cam

    def active_in(self, incident_id: str) -> list[Camera]:
        with self._lock:
            return [c for c in self._cams if c.incident_id == incident_id and c.active]

    def by_token(self, token: str) -> Optional[Camera]:
        with self._lock:
            return next((c for c in self._cams if c.token and secrets.compare_digest(c.token, token)), None)

    def opened(self, cam: Camera) -> None:
        """The caller opened the link: the camera call's clock starts now."""
        with self._lock:
            if cam.status == "invited":
                cam.status, cam.started_at = "streaming", self._clock()

    def get(self, incident_id: str, call_id: str) -> Optional[Camera]:
        with self._lock:
            return next((c for c in self._cams if c.incident_id == incident_id and c.call_id == call_id), None)

    def elapsed(self, cam: Camera) -> float:
        return round(self._clock() - cam.started_at, 1)

    def end(self, cam: Camera) -> None:
        with self._lock:
            if cam.active:
                cam.status, cam.ended_at = "ended", self._clock()

    def clear(self) -> list[Camera]:
        """Clear call: stop every streaming camera."""
        with self._lock:
            ended = [c for c in self._cams if c.active]
            for c in ended:
                c.status, c.ended_at = "ended", self._clock()
        return ended
