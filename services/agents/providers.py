"""Data provider adapters (PRD 7.4).

The Protocols are the PRD's. The fixture implementations read
data/fixtures/<building_id>/{costar,matterport,vexcel}.json, whose contents use
only our own normalized fields (schema Fact / Marker / Evidence, exposures):

  costar.json      property_id, name, address, coordinates, facts, tenants, evidence
  matterport.json  model {model_id, captured_at} or null, floorplans[], frames[]
                   (each record: floor, evidence, the markers read from it)
  vexcel.json      center {lat, lon}, aerials[] (each: evidence, markers, exposures)

The Live* adapters read the hero buildings' stored real API responses in
data/raw/<id>/ (gitignored sponsor data) and fall back to the fixtures for every
other id. They make no network calls: calling the vendor APIs is not configured
(DATA_MODE=live logs that once), so the demo never depends on API uptime.
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

_SUFFIXES = {"blvd": "boulevard", "st": "street", "ave": "avenue", "rd": "road", "dr": "drive", "pl": "place",
             "ln": "lane", "ct": "court"}


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
        wanted = tokens(query) - set(_SUFFIXES.values())  # "street" alone is not a match
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


# ---------- stored real API responses (data/raw/<id>/) ----------
#
# The hero buildings' real CoStar records, Matterport plan and Vexcel coverage metadata, stored as
# returned (data/raw/<id>/README.md says where each file comes from; FIELD_NOTES.md lists the
# quirks). Sponsor data: gitignored, never committed. Only fields that exist there are read, and
# they are mapped to the same normalized shapes the fixtures use. Not read, on purpose: the plan's
# quality tags and bedroom-window checks (a missing opening in a scan is not a missing window).

RAW_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw"
EVENT_COMPLETE, EVENT_RENOVATED = 2, 4   # constructionEvents eventType (as in tools/costar_kit.py)


def _raw(building_id: str, *parts: str) -> Optional[Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", building_id or ""):
        return None
    path = RAW_ROOT.joinpath(building_id, *parts)
    return path if path.exists() else None


def _raw_ids(layer: str) -> list[str]:
    return sorted(p.parent.name for p in RAW_ROOT.glob(f"*/{layer}")) if RAW_ROOT.is_dir() else []


def _load(path: Path):
    return json.loads(path.read_text())


def _fact(key: str, value, evidence_id: str, confidence: float, status: str = "verified",
          as_of: Optional[str] = None) -> dict:
    return {"key": key, "value": value, "status": status, "confidence": confidence,
            "evidence_ids": [evidence_id] if evidence_id else [], "as_of": as_of}


def _first_sentence(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]


def sprinkler_fact(home_security: list[str], lot_mentions: list[str], evidence_id: str) -> dict:
    """Residential listings (FIELD_NOTES.md): only "Fire Sprinkler..." under Home Security is a fire
    sprinkler system. "Sprinkler System" under Lot Details, or in the remarks, is lawn irrigation:
    no claim either way."""
    if any(re.search(r"\bfire sprinkler", f, re.I) for f in home_security):
        return _fact("building.sprinklered", True, evidence_id, 0.8)
    reason = ("not stated; the listing's 'Sprinkler System' is lawn irrigation, not fire protection"
              if lot_mentions else "not stated in the listing")
    return {"key": "building.sprinklered", "value": reason, "status": "unverified", "confidence": 0.0,
            "evidence_ids": []}


def _residential(bid: str, folder: Path) -> dict:
    listing = _load(folder / "listing.json")
    features_path = folder / "listing_features.json"
    features = _load(features_path).get("features", []) if features_path.exists() else []
    prop, structure = listing.get("property", {}), (listing.get("resoValues") or {}).get("structure") or {}
    addr = prop.get("address") or {}
    as_of = listing.get("listDate")
    ev, evf = "ev_cs_listing", "ev_cs_features"
    evidence = {ev: {"id": ev, "kind": "costar_record", "source": "costar", "captured_at": as_of,
                     "uri": f"costar-kit://residential-listings/{bid}",
                     "locator": {"file": f"data/raw/{bid}/costar/listing.json", "listing_id": bid}}}
    if features:
        evidence[evf] = {"id": evf, "kind": "costar_record", "source": "costar", "captured_at": as_of,
                         "uri": f"costar-kit://residential-listing-features/{bid}",
                         "locator": {"file": f"data/raw/{bid}/costar/listing_features.json", "listing_id": bid}}
    facts = {}

    def put(key, value, evidence_id=ev, confidence=0.85):
        if value is not None:
            facts[key] = _fact(key, value, evidence_id, confidence, as_of=as_of)

    put("building.use", prop.get("mlsPropertyType"), confidence=0.9)
    put("building.stories", structure.get("stories"))           # often missing (FIELD_NOTES.md)
    put("building.year_built", prop.get("yearBuilt"), confidence=0.9)
    put("building.bedrooms", prop.get("bedsMin"))
    if prop.get("bathsFull") is not None:                       # full + half, to compare with plan rooms
        put("building.bathrooms", prop["bathsFull"] + (prop.get("bathsHalf") or 0))
    put("building.area_sqft", prop.get("sqftMin"))
    put("building.garage_spaces", structure.get("garageSpaces"))
    security = [f["name"] for f in features if f.get("subcategory1") == "Home Security"]
    lot = [f["name"] for f in features if f.get("subcategory1") == "Lot Details" and "sprinkler" in f["name"].lower()]
    if re.search(r"sprinkler", listing.get("remarks") or "", re.I):
        lot.append("remarks")
    facts["building.sprinklered"] = sprinkler_fact(security, lot, evf if features else ev)
    if any(re.search(r"smoke detector", f, re.I) for f in security):
        put("building.smoke_detectors", True, evf, 0.8)
    street = addr.get("fullStreetAddress") or ""
    return {"property_id": bid, "name": street, "address": street,
            "coordinates": {"lat": addr.get("latitude"), "lon": addr.get("longitude")},
            "facts": facts, "tenants": [], "evidence": evidence}


def _commercial(bid: str, folder: Path) -> dict:
    prop = _load(folder / "property.json")
    a = prop.get("primaryAddress") or (prop.get("addresses") or [{}])[0]
    ev = "ev_cs_property"
    evidence = {ev: {"id": ev, "kind": "costar_record", "source": "costar",
                     "uri": f"costar-kit://commercial-property/{bid}",
                     "locator": {"file": f"data/raw/{bid}/costar/property.json", "property_id": bid}}}
    facts = {}

    def put(key, value, confidence=0.85):
        if value is not None:
            facts[key] = _fact(key, value, ev, confidence)

    events = {e.get("eventType"): e.get("year") for e in prop.get("constructionEvents") or []}
    flex = prop.get("propertyFlex") or {}
    bays = flex.get("driveInBays") or {}
    put("building.name", prop.get("buildingName"), 0.95)
    put("building.description", _first_sentence(((prop.get("notes") or [{}])[0]).get("value")), 0.8)
    put("building.stories", prop.get("numStoriesAboveGrade"), 0.9)
    put("building.area_sqft", prop.get("grossBuildingAreaSquareFeet"), 0.9)
    put("building.year_built", events.get(EVENT_COMPLETE), 0.9)
    put("building.year_renovated", events.get(EVENT_RENOVATED))
    put("building.sprinklered", prop.get("hasSprinklers"), 0.85)   # a fire-protection field of the record
    if bays.get("numDriveInBays"):
        size = (f", {bays['numFeetWide']} x {bays['numFeetHigh']} ft" if bays.get("numFeetWide") and bays.get("numFeetHigh")
                else "")
        put("building.drive_in_bays", f"{bays['numDriveInBays']}{size}")
    put("building.loading_docks", flex.get("numExternalLoadingDocks"))
    put("building.ceiling_height_ft", (flex.get("structural") or {}).get("ceilingHeightFeet"))
    tenants = []
    companies = folder / "tenant_companies.json"
    if companies.exists():
        evidence["ev_cs_tenants"] = {"id": "ev_cs_tenants", "kind": "costar_record", "source": "costar",
                                     "uri": f"costar-kit://company-detail/{bid}",
                                     "locator": {"file": f"data/raw/{bid}/costar/tenant_companies.json"}}
        for c in _load(companies):
            staff = f" ({c['numEmployees']} employees)" if c.get("numEmployees") else ""
            about = _first_sentence((c.get("profile") or {}).get("locationBio"))
            tenants.append(_fact(f"tenant.{c['id']}", f"{c['name'].strip()}{staff}" + (f": {about}" if about else ""),
                                 "ev_cs_tenants", 0.75))
    street = " ".join(str(a[k]) for k in ("streetNumber", "streetName", "streetType") if a.get(k))
    return {"property_id": bid, "name": prop.get("buildingName") or street, "address": street,
            "coordinates": {"lat": a.get("latitude"), "lon": a.get("longitude")},
            "facts": facts, "tenants": tenants, "evidence": evidence}


def raw_property(bid: str) -> Optional[dict]:
    folder = _raw(bid, "costar")
    if folder is None:
        return None
    if (folder / "listing.json").exists():
        return _residential(bid, folder)
    if (folder / "property.json").exists():
        return _commercial(bid, folder)
    return None


def _ev_id(original: str) -> str:
    return "ev_mp_" + original.removeprefix("ev_")


def raw_plan(bid: str) -> Optional[dict]:
    """The normalized Matterport plan.json, mapped to our matterport layer (model + facts + markers)."""
    path = _raw(bid, "matterport", "plan.json")
    if path is None:
        return None
    plan = _load(path)
    cited: set[str] = set()

    def ids(evidence_ids) -> list[str]:
        out = [e for e in evidence_ids or [] if e in plan.get("evidence", {})
               and plan["evidence"][e].get("source") in ("matterport", "rules")]
        cited.update(out)
        return [_ev_id(e) for e in out]

    facts = {}
    for f in plan.get("facts", []):
        if f.get("key") and f.get("value") is not None:
            facts[f["key"]] = {"key": f["key"], "value": f["value"], "status": f["status"],
                               "confidence": f["confidence"], "evidence_ids": ids(f.get("evidence_ids"))}
    rooms = {r["id"]: r for r in plan.get("rooms", [])}
    markers = []
    for x in plan.get("exits", []):
        room = rooms.get(x.get("from_room")) or {}
        name = (room.get("label") or room.get("type") or "").lower()
        where = f" from the {name}" if name and name != "other" else ""
        markers.append({"id": f"m_exit_{x['door_id']}", "kind": "exit", "floor": x.get("floor"),
                        "label": f"Exterior door{where}, floor {x.get('floor')}",
                        "status": x.get("status", "inferred"), "confidence": x.get("confidence", 0.7),
                        "evidence_ids": ids(x.get("evidence_ids"))})
    for st in plan.get("stairs", []):
        markers.append({"id": st["id"], "kind": "stair", "floor": st.get("lower_floor"),
                        "label": f"Stairs between floor {st.get('lower_floor')} and floor {st.get('upper_floor')}"
                                 + ("" if st.get("state") == "ok" else f" (footprint {st.get('state')})"),
                        "status": st.get("status", "inferred"), "confidence": st.get("confidence", 0.7),
                        "evidence_ids": ids(st.get("evidence_ids"))})
    evidence = {_ev_id(e): {**plan["evidence"][e], "id": _ev_id(e)} for e in sorted(cited)}
    return {"model_id": plan.get("model_id"), "captured_at": None,   # no scan capture date in the data
            "facts": facts, "evidence": evidence, "markers": markers}


def raw_aerials(bid: str) -> list[dict]:
    """Vexcel coverage metadata: one evidence item per capture. No image is stored, so no markers."""
    path = _raw(bid, "vexcel", "metadata.json")
    if path is None:
        return []
    meta = _load(path)
    views = [("ortho", "overhead (ortho)", meta.get("ortho") or {})]
    views += [(f"oblique_{d}", f"oblique, camera facing {d}", o) for d, o in (meta.get("oblique") or {}).items()]
    out = []
    for key, view, v in views:
        if not v:
            continue
        out.append({"evidence": {"id": f"ev_vx_{key}", "kind": "aerial_crop", "source": "vexcel",
                                 "captured_at": v.get("captureDate"),
                                 "locator": {"view": view, "collection": v.get("collection"), "layer": v.get("layer"),
                                             "box_m": v.get("bbox_meters"), "images_stored": False,
                                             "note": "coverage metadata only (Vexcel API 2.0); no image was analysed"}},
                    "markers": [], "exposures": []})
    return out


class LiveCoStarProvider:
    """CoStar from the stored kit records in data/raw/<id>/costar/, then the fixtures."""

    def __init__(self, fixture: "FixtureCoStarProvider"):
        self._fixture = fixture

    def search_property(self, query: str) -> list[dict]:
        wanted = tokens(query) - set(_SUFFIXES.values())
        found = []
        for bid in _raw_ids("costar"):
            p = raw_property(bid)
            if p and wanted & (tokens(p["name"]) | tokens(p["address"])):
                found.append({k: p[k] for k in ("property_id", "name", "address", "coordinates")})
        return found + self._fixture.search_property(query)

    def get_property(self, property_id: str) -> dict:
        return raw_property(property_id) or self._fixture.get_property(property_id)


class LiveMatterportProvider:
    """Matterport from the stored normalized plan.json in data/raw/<id>/matterport/, then the fixtures."""

    def __init__(self, fixture: "FixtureMatterportProvider"):
        self._fixture = fixture

    def find_model(self, property: dict) -> Optional[dict]:
        plan = raw_plan(property.get("property_id", ""))
        if plan is not None:
            return {k: plan[k] for k in ("model_id", "captured_at", "facts", "evidence")}
        if _raw(property.get("property_id", ""), "costar"):  # a hero without a stored scan (23296)
            return None
        return self._fixture.find_model(property)

    def _plan_for(self, model_id: str) -> Optional[dict]:
        for bid in _raw_ids("matterport"):
            plan = raw_plan(bid)
            if plan and plan["model_id"] == model_id:
                return plan
        return None

    def get_floorplans(self, model_id: str) -> list[dict]:
        plan = self._plan_for(model_id)
        if plan is None:
            return self._fixture.get_floorplans(model_id)
        main = plan["evidence"].get("ev_mp_plan") or next(iter(plan["evidence"].values()))
        return [{"floor": None, "evidence": main, "extra_evidence": list(plan["evidence"].values()),
                 "markers": plan["markers"]}]

    def get_frames(self, model_id: str, floor: Optional[int] = None) -> list[dict]:
        return [] if self._plan_for(model_id) else self._fixture.get_frames(model_id, floor)


class LiveVexcelProvider:
    """Vexcel coverage from the stored metadata in data/raw/<id>/vexcel/, then the fixtures."""

    def __init__(self, fixture: "FixtureVexcelProvider"):
        self._fixture = fixture

    def get_aerial(self, lat: float, lon: float, radius_m: int) -> list[dict]:
        out = []
        for bid in _raw_ids("vexcel"):
            loc = _load(RAW_ROOT / bid / "vexcel" / "metadata.json").get("location") or {}
            if loc.get("lat") is not None and distance_m(lat, lon, loc["lat"], loc["lon"]) <= radius_m:
                out += raw_aerials(bid)
        return out or self._fixture.get_aerial(lat, lon, radius_m)


@dataclass
class Providers:
    costar: CoStarProvider
    matterport: MatterportProvider
    vexcel: VexcelProvider


_LIVE_WARNED = False


def get_providers(root: Optional[Path] = None) -> Providers:
    """Stored real responses (data/raw) first, synthetic fixtures second, in every data mode.

    DATA_MODE=live means calling the vendor APIs, which is not configured: it logs that once.
    """
    global _LIVE_WARNED
    if os.environ.get("DATA_MODE", "fixture").strip().lower() == "live" and not _LIVE_WARNED:
        log.warning("Live API calls are not configured; using stored API responses (data/raw) and fixtures")
        _LIVE_WARNED = True
    return Providers(LiveCoStarProvider(FixtureCoStarProvider(root)),
                     LiveMatterportProvider(FixtureMatterportProvider(root)),
                     LiveVexcelProvider(FixtureVexcelProvider(root)))
