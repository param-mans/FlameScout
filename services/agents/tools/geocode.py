"""Geocoding for the locator (PRD FR-3). Opt-in: GEOCODER=nominatim.

Nominatim (OpenStreetMap) public search API. Fields used are the documented
`/search?format=jsonv2` ones: lat, lon (strings), display_name, importance.
Usage policy: <= 1 request/second, identifying User-Agent, cache results.
The User-Agent is a generic app name - never put personal details in it.
"""
from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from typing import Optional, Protocol

import httpx

log = logging.getLogger("flamescout.geocode")


class Geocoder(Protocol):
    def geocode(self, query: str) -> list[dict]: ...   # [{"lat", "lon", "label", "score"}]


class NominatimGeocoder:
    URL = "https://nominatim.openstreetmap.org/search"
    USER_AGENT = "FlameScout-hackathon-demo/0.1"

    def __init__(self, url: Optional[str] = None, timeout_s: float = 4.0):
        self.url = url or self.URL
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._last = 0.0

    def geocode(self, query: str) -> list[dict]:
        return list(self._cached(query.strip().lower()))

    @lru_cache(maxsize=256)
    def _cached(self, query: str) -> tuple[dict, ...]:
        with self._lock:                      # 1 req/s policy
            wait = 1.0 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        r = httpx.get(self.url, params={"q": query, "format": "jsonv2", "limit": 5},
                      headers={"User-Agent": self.USER_AGENT}, timeout=self.timeout_s)
        r.raise_for_status()
        return tuple({"lat": float(x["lat"]), "lon": float(x["lon"]),
                      "label": x.get("display_name", ""),
                      "score": float(x.get("importance") or 0.0)} for x in r.json())


def get_geocoder(env: dict[str, str]) -> Optional[Geocoder]:
    kind = (env.get("GEOCODER") or "").lower()
    if kind == "nominatim":
        return NominatimGeocoder(env.get("NOMINATIM_URL") or None)
    return None
