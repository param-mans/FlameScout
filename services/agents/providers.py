"""Data provider adapters (PRD 7.4).

The Protocols are the PRD's. The fixture implementations read
data/fixtures/<building_id>/{costar,matterport,vexcel}.json, whose contents use
only our own normalized fields (schema Fact / Marker / Evidence, exposures):

  costar.json      property_id, name, address, coordinates, facts, tenants, evidence
  matterport.json  model {model_id, captured_at} or null, floorplans[], frames[]
                   (each record: floor, evidence, the markers read from it)
  vexcel.json      center {lat, lon}, aerials[] (each: evidence, markers, exposures)

The live adapters are stubs: no HTTP code and no vendor field names, because we
have no confirmed API contract yet. With DATA_MODE=live they log "not
configured" once and answer from the fixtures, so the demo never depends on
API uptime.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"

_SUFFIXES = {"blvd": "boulevard", "st": "street", "ave": "avenue", "rd": "road", "dr": "drive", "pl": "place"}


class CoStarProvider(Protocol):
    def search_property(self, query: str) -> list[dict]: ...
    def get_property(self, property_id: str) -> dict: ...


class MatterportProvider(Protocol):
    def find_model(self, property: dict) -> Optional[dict]: ...        # model id, capture date
    def get_floorplans(self, model_id: str) -> list[dict]: ...
    def get_frames(self, model_id: str, floor: Optional[int]) -> list[dict]: ...


class VexcelProvider(Protocol):
    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]: ...


def tokens(text: str) -> set[str]:
    """Lowercase words of an address or name, with street suffixes spelled out."""
    return {_SUFFIXES.get(t, t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t != "the"}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


# ---------- fixtures ----------

class _FixtureFiles:
    layer = ""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else FIXTURES

    # Files with a "_fixture" key are the services/api pipeline's fixtures (tools/providers.py),
    # a different format in the same folder: this adapter never serves them.
    def _read(self, building_id: str) -> Optional[dict]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", building_id):
            raise ValueError(f"Bad building id {building_id!r}")
        path = self.root / building_id / f"{self.layer}.json"
        data = json.loads(path.read_text()) if path.exists() else None
        return None if data is None or "_fixture" in data else data

    def _all(self) -> list[dict]:
        docs = (json.loads(p.read_text()) for p in sorted(self.root.glob(f"*/{self.layer}.json")))
        return [d for d in docs if "_fixture" not in d]


class FixtureCoStarProvider(_FixtureFiles):
    layer = "costar"

    def search_property(self, query: str) -> list[dict]:
        """Properties whose name or address shares a word with the query."""
        wanted = tokens(query)
        return [{k: p[k] for k in ("property_id", "name", "address", "coordinates")}
                for p in self._all() if wanted & (tokens(p["name"]) | tokens(p["address"]))]

    def get_property(self, property_id: str) -> dict:
        data = self._read(property_id)
        if data is None:
            raise FileNotFoundError(f"No CoStar fixture for property {property_id!r} under {self.root}")
        return data


class FixtureMatterportProvider(_FixtureFiles):
    layer = "matterport"

    def find_model(self, property: dict) -> Optional[dict]:
        data = self._read(property["property_id"])
        return data.get("model") if data else None

    def _model(self, model_id: str) -> dict:
        for data in self._all():
            if (data.get("model") or {}).get("model_id") == model_id:
                return data
        raise FileNotFoundError(f"No Matterport fixture for model {model_id!r} under {self.root}")

    def get_floorplans(self, model_id: str) -> list[dict]:
        return self._model(model_id).get("floorplans", [])

    def get_frames(self, model_id: str, floor: Optional[int] = None) -> list[dict]:
        return [f for f in self._model(model_id).get("frames", []) if floor is None or f.get("floor") == floor]


class FixtureVexcelProvider(_FixtureFiles):
    layer = "vexcel"

    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        return [aerial for data in self._all()
                if distance_m(lat, lon, data["center"]["lat"], data["center"]["lon"]) <= radius_m
                for aerial in data.get("aerials", [])]


# ---------- live stubs ----------

class _LiveStub:
    vendor = ""

    def __init__(self, fixture):
        self._fixture = fixture
        self._warned = False

    def _fall_back(self):
        if not self._warned:
            log.warning("%s live adapter not configured; using fixtures", self.vendor)
            self._warned = True
        return self._fixture


class LiveCoStarProvider(_LiveStub):
    vendor = "CoStar"

    def search_property(self, query: str) -> list[dict]:
        return self._fall_back().search_property(query)

    def get_property(self, property_id: str) -> dict:
        return self._fall_back().get_property(property_id)


class LiveMatterportProvider(_LiveStub):
    vendor = "Matterport"

    def find_model(self, property: dict) -> Optional[dict]:
        return self._fall_back().find_model(property)

    def get_floorplans(self, model_id: str) -> list[dict]:
        return self._fall_back().get_floorplans(model_id)

    def get_frames(self, model_id: str, floor: Optional[int] = None) -> list[dict]:
        return self._fall_back().get_frames(model_id, floor)


class LiveVexcelProvider(_LiveStub):
    vendor = "Vexcel"

    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        return self._fall_back().get_aerial(lat, lon, radius_m)


@dataclass
class Providers:
    costar: CoStarProvider
    matterport: MatterportProvider
    vexcel: VexcelProvider


def get_providers(root: Optional[Path] = None) -> Providers:
    """DATA_MODE=fixture (default) or live. Live is stubs over the same fixtures for now."""
    p = Providers(FixtureCoStarProvider(root), FixtureMatterportProvider(root), FixtureVexcelProvider(root))
    if os.environ.get("DATA_MODE", "fixture").strip().lower() == "live":
        p = Providers(LiveCoStarProvider(p.costar), LiveMatterportProvider(p.matterport),
                      LiveVexcelProvider(p.vexcel))
    return p
