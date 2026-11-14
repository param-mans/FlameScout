"""Data provider adapters (PRD 7.4). DATA_MODE=live|fixture.

Every adapter returns the NORMALIZED dict shapes below. Live adapters must map
the vendor response into these shapes; nothing downstream sees raw vendor fields.

    CoStar search_property(q) -> [{"property_id", "name", "address", "lat", "lon", "score"}]
    CoStar get_property(id)   -> {"property_id", "name", "address", "lat", "lon",
                                  "use"?, "stories"?, "year_built"?, "area_sqft"?,
                                  "construction"?, "sprinklered"?, "as_of"?, "record_uri"?,
                                  "tenants": [{"name", "category"?}], "matterport_model_id"?}
    Matterport find_model(prop)  -> {"model_id", "captured_at"} | None
    Matterport get_floorplans(id)-> [{"floor", "uri", "detections"?}]
    Matterport get_frames(id, f) -> [{"frame_id", "floor", "uri", "detections"?}]
    Vexcel get_aerial(lat, lon, r) -> [{"image_id", "uri", "captured_at", "detections"?}]

`detections` = already-extracted labels (Matterport annotations or a cached
vision pass): [{"kind", "label", "floor"?, "scan_position"?, "lat"?, "lon"?,
"bbox"?, "confidence", "source": "annotation"|"vision"}]. Items without
detections get one Gemini vision pass (services/agents/tools/vision.py).

Optional keys absent from a record stay absent: the dossier reports them as gaps.

COSTAR KIT: CoStar's real data arrives as a static kit, not an API. When
COSTAR_INDEX points at the index built by scripts/build_costar_index.py
(normalization in costar_kit.py), CoStar is served from it in EVERY mode, and
the synthetic fixture buildings still resolve alongside (scripted calls S1-S4).
CoStar adds keys beyond the list above: height_ft, year_renovated, sprinkler_type,
units, num_buildings, elevators, walkup, construction_status, photo_uri,
as_of_basis, tenants[].floor, tenants_total, matterport_model_ids.

LIVE ADAPTERS: Matterport / Vexcel field names and endpoints are not confirmed
yet (PRD Section 20, Phase 0 smoke test). Until they are, the live adapters
raise ProviderUnavailable naming what is missing and the FallbackProvider
serves fixtures. Do NOT fill them in from memory.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .geocode import Geocoder, get_geocoder

log = logging.getLogger("flamescout.providers")

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ABBREV = {"boulevard": "blvd", "street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
           "place": "pl", "north": "n", "south": "s", "east": "e", "west": "w", "centre": "center"}
_STOP = {"the", "a", "of", "at", "building", "ca", "usa"}


class ProviderUnavailable(RuntimeError):
    pass


class CoStarProvider(Protocol):
    def search_property(self, query: str) -> list[dict]: ...
    def get_property(self, property_id: str) -> dict: ...


class MatterportProvider(Protocol):
    def find_model(self, property: dict) -> Optional[dict]: ...
    def get_floorplans(self, model_id: str) -> list[dict]: ...
    def get_frames(self, model_id: str, floor: Optional[int]) -> list[dict]: ...


class VexcelProvider(Protocol):
    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]: ...


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {_ABBREV.get(w, w) for w in words if w not in _STOP}


def match_score(query: str, *targets: str) -> float:
    """Share of query tokens found in the best target. Deterministic, 0..1."""
    q = tokens(query)
    if not q:
        return 0.0
    return max((len(q & tokens(t)) / len(q) for t in targets if t), default=0.0)


# ---------------- fixtures ----------------

class _FixtureStore:
    def __init__(self, root: Path):
        self.root = root

    def buildings(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/costar.json"))

    def load(self, building_id: str, name: str) -> Optional[dict]:
        if not SAFE_ID.match(building_id):
            raise KeyError(building_id)
        p = self.root / building_id / f"{name}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def uri(self, building_id: str, name: str) -> str:
        return f"fixture://{building_id}/{name}.json"


class FixtureCoStarProvider:
    def __init__(self, root: Path):
        self.fx = _FixtureStore(root)

    def search_property(self, query: str) -> list[dict]:
        out = []
        for bid in self.fx.buildings():
            p = self.fx.load(bid, "costar") or {}
            score = match_score(query, p.get("name", ""), p.get("address", ""), *p.get("aliases", []))
            if score >= 0.5:
                out.append({"property_id": bid, "name": p.get("name"), "address": p.get("address"),
                            "lat": p.get("lat"), "lon": p.get("lon"), "score": round(score, 3)})
        return sorted(out, key=lambda r: -r["score"])

    def get_property(self, property_id: str) -> dict:
        p = self.fx.load(property_id, "costar")
        if p is None:
            raise KeyError(property_id)
        p = {k: v for k, v in p.items() if not k.startswith("_")}
        p.setdefault("property_id", property_id)
        p.setdefault("record_uri", self.fx.uri(property_id, "costar"))
        p.setdefault("tenants", [])
        return p


class KitCoStarProvider:
    """Real CoStar records from the normalized kit index (one JSON record per line)."""

    MAX_RESULTS = 10

    def __init__(self, index_path: Path):
        self.path = index_path
        self._by_id: Optional[dict[str, dict]] = None

    def _records(self) -> dict[str, dict]:
        if self._by_id is None:
            with self.path.open() as f:
                recs = (json.loads(line) for line in f if line.strip())
                self._by_id = {r["property_id"]: r for r in recs}
        return self._by_id

    def has(self, property_id: str) -> bool:
        return property_id in self._records()

    def search_property(self, query: str) -> list[dict]:
        out = []
        for p in self._records().values():
            score = match_score(query, p.get("name", ""), p.get("address", ""), *p.get("aliases", []))
            if score >= 0.5:
                out.append({"property_id": p["property_id"], "name": p.get("name"), "address": p.get("address"),
                            "lat": p.get("lat"), "lon": p.get("lon"), "score": round(score, 3)})
        return sorted(out, key=lambda r: -r["score"])[:self.MAX_RESULTS]

    def get_property(self, property_id: str) -> dict:
        p = self._records().get(property_id)
        if p is None:
            raise KeyError(property_id)
        return {**p, "tenants": list(p.get("tenants", []))}


class CombinedCoStarProvider:
    """Kit records first; synthetic fixture buildings still resolve (demo call scripts)."""

    def __init__(self, kit: KitCoStarProvider, fixture: FixtureCoStarProvider):
        self.kit, self.fixture = kit, fixture

    def search_property(self, query: str) -> list[dict]:
        hits = self.kit.search_property(query) + self.fixture.search_property(query)
        return sorted(hits, key=lambda r: -r["score"])

    def get_property(self, property_id: str) -> dict:
        if self.kit.has(property_id):
            return self.kit.get_property(property_id)
        return self.fixture.get_property(property_id)


class FixtureMatterportProvider:
    def __init__(self, root: Path):
        self.fx = _FixtureStore(root)
        self._by_model: dict[str, dict] = {}

    def find_model(self, property: dict) -> Optional[dict]:
        m = self.fx.load(property["property_id"], "matterport")
        if not m or not m.get("model_id"):
            return None
        self._by_model[m["model_id"]] = m
        return {"model_id": m["model_id"], "captured_at": m.get("captured_at"),
                "uri": self.fx.uri(property["property_id"], "matterport")}

    def get_floorplans(self, model_id: str) -> list[dict]:
        return list(self._by_model.get(model_id, {}).get("floorplans", []))

    def get_frames(self, model_id: str, floor: Optional[int]) -> list[dict]:
        frames = self._by_model.get(model_id, {}).get("frames", [])
        return [f for f in frames if floor is None or f.get("floor") == floor]


class FixtureVexcelProvider:
    def __init__(self, root: Path):
        self.fx = _FixtureStore(root)

    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        from .rules import haversine_m
        for bid in self.fx.buildings():
            p = self.fx.load(bid, "costar") or {}
            if p.get("lat") is None or haversine_m(lat, lon, p["lat"], p["lon"]) > 50:
                continue
            v = self.fx.load(bid, "vexcel") or {}
            return [{**img, "uri": img.get("uri") or self.fx.uri(bid, "vexcel")}
                    for img in v.get("images", [])]
        return []


# ---------------- live (pending Phase 0) ----------------

class _PendingLive:
    """Live adapter placeholder: refuses until the real API contract is confirmed."""
    vendor = "?"
    env_key = "?"

    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key

    def _unavailable(self, method: str) -> Any:
        if not self.api_key:
            raise ProviderUnavailable(f"{self.vendor}.{method}: {self.env_key} not set")
        raise ProviderUnavailable(
            f"{self.vendor}.{method}: live mapping not implemented - confirm endpoint and field "
            f"names from the toolkit (PRD 20), then map into the normalized shape in providers.py")


class LiveCoStarProvider(_PendingLive):
    vendor, env_key = "costar", "COSTAR_API_KEY"

    def search_property(self, query: str) -> list[dict]:
        return self._unavailable("search_property")

    def get_property(self, property_id: str) -> dict:
        return self._unavailable("get_property")


class LiveMatterportProvider(_PendingLive):
    vendor, env_key = "matterport", "MATTERPORT_API_KEY"

    def find_model(self, property: dict) -> Optional[dict]:
        return self._unavailable("find_model")

    def get_floorplans(self, model_id: str) -> list[dict]:
        return self._unavailable("get_floorplans")

    def get_frames(self, model_id: str, floor: Optional[int]) -> list[dict]:
        return self._unavailable("get_frames")


class LiveVexcelProvider(_PendingLive):
    vendor, env_key = "vexcel", "VEXCEL_API_KEY"

    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        return self._unavailable("get_aerial")


class FallbackProvider:
    """Calls the live adapter; on any failure logs it and serves the fixture."""

    def __init__(self, name: str, live: Any, fixture: Any, on_miss: Callable[[str], None]):
        self._name, self._live, self._fixture, self._on_miss = name, live, fixture, on_miss

    def __getattr__(self, method: str) -> Callable[..., Any]:
        live_fn, fx_fn = getattr(self._live, method), getattr(self._fixture, method)

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return live_fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - demo must never block on a vendor
                msg = f"{self._name}.{method} live failed ({e}); using fixture"
                log.warning(msg)
                self._on_miss(msg)
                return fx_fn(*args, **kwargs)
        return call


@dataclass
class Providers:
    mode: str
    costar: CoStarProvider
    matterport: MatterportProvider
    vexcel: VexcelProvider
    geocoder: Optional[Geocoder]
    misses: list[str] = field(default_factory=list)   # live calls that fell back


def costar_kit(env: dict[str, str], root: Optional[Path] = None) -> Optional[KitCoStarProvider]:
    """The kit index named by COSTAR_INDEX (relative paths resolve against the repo root)."""
    raw = env.get("COSTAR_INDEX")
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (root or Path(__file__).resolve().parents[3]) / p
    if not p.exists():
        log.warning("COSTAR_INDEX=%s not found - run scripts/build_costar_index.py; using fixtures", raw)
        return None
    return KitCoStarProvider(p)


def get_providers(mode: str, fixtures_root: Path, env: dict[str, str]) -> Providers:
    fx = (FixtureCoStarProvider(fixtures_root), FixtureMatterportProvider(fixtures_root),
          FixtureVexcelProvider(fixtures_root))
    kit = costar_kit(env)
    costar: CoStarProvider = CombinedCoStarProvider(kit, fx[0]) if kit else fx[0]
    if mode != "live":
        return Providers("fixture", costar, fx[1], fx[2], geocoder=None)
    misses: list[str] = []
    rec = misses.append
    return Providers(
        "live",
        costar if kit else FallbackProvider("costar", LiveCoStarProvider(env.get("COSTAR_API_KEY")), fx[0], rec),
        FallbackProvider("matterport", LiveMatterportProvider(env.get("MATTERPORT_API_KEY")), fx[1], rec),
        FallbackProvider("vexcel", LiveVexcelProvider(env.get("VEXCEL_API_KEY")), fx[2], rec),
        geocoder=get_geocoder(env), misses=misses)
