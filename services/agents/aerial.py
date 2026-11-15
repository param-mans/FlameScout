"""Vexcel aerial images: one Gemini vision reading per image, then geometry in code.

The model only labels what it sees, with a box in Gemini's native convention (box_2d =
[ymin, xmin, ymax, xmax], 0-1000) and a confidence. Everything measured is computed here:
the ortho is north-up and covers bbox_meters x bbox_meters centred on the building's point, so a
box converts to metres and to lat/lon; gaps between the target and its neighbours become
exposure distances; the stories seen in the obliques are reconciled across views.

    read_building(building_id)   vision, once per image -> data/raw/<id>/derived/aerial_reading.json
    records(building_id)         no LLM: the stored reading -> provider records (evidence, markers,
                                 exposures, exterior_entries, facts) for providers.raw_aerials

The images come from scripts/fetch_vexcel.py (data/raw/<id>/vexcel/, sponsor data, never committed).
"""
from __future__ import annotations

import base64
import json
import logging
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .llm import configured, model_name, structured

log = logging.getLogger("flamescout.aerial")

RAW_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw"
READING_FILE = "aerial_reading.json"
PROMPT_VERSION = 1
EXPOSURE_MAX_M = 30.0     # neighbours further than this are not exposures
MIN_CONFIDENCE = 0.4      # vision labels below this are dropped
ENTRY_CONFIDENCE_CAP = 0.5   # entries read from an oblique stay low confidence
GEOMETRY_CONFIDENCE_CAP = 0.6  # box gaps approximate footprints: never better than this
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
M_PER_DEG_LAT = 111_320.0


# ---------- what the model returns ----------

Box = list[int]
_BOX = Field(description="[ymin, xmin, ymax, xmax], each 0-1000, relative to the whole image", min_length=4, max_length=4)


class Seen(BaseModel):
    label: str = Field(description="Short description of what is visible, e.g. 'two-car driveway from the street'")
    box_2d: Box = _BOX
    confidence: float = Field(ge=0, le=1)


class RoofFeature(Seen):
    kind: Literal["solar_panels", "skylight", "roof_hatch", "hvac_unit", "other"]


class Structure(Seen):
    kind: Literal["house", "garage_or_shed", "commercial", "other"]


class SiteFeature(Seen):
    kind: Literal["driveway", "gate", "fence", "pool", "power_line", "parking", "other"]


class OrthoReading(BaseModel):
    target_found: bool = Field(description="True if a building sits at the exact centre of the image")
    target: Optional[Box] = Field(default=None, description="box_2d of that central building's roof outline")
    target_confidence: float = Field(default=0, ge=0, le=1)
    roof: Literal["flat", "pitched", "mixed", "unknown"] = "unknown"
    roof_features: list[RoofFeature] = []
    neighbours: list[Structure] = Field(default=[], description="Other buildings, never the central one")
    site: list[SiteFeature] = Field(default=[], description="Features on the central building's lot or its edge")


class Entry(Seen):
    kind: Literal["front_door", "garage_door", "side_door", "patio_door", "other"]


class ObliqueReading(BaseModel):
    target_visible: bool = Field(description="True if the building at the centre of the image is clearly visible")
    stories: Optional[int] = Field(default=None, ge=1, le=60,
                                   description="Above-ground storeys of the central building, only if countable")
    stories_confidence: float = Field(default=0, ge=0, le=1)
    entries: list[Entry] = Field(default=[], description="Doors of the central building on the facade facing the camera")


_SYSTEM = ("You read aerial images for a fire department. Report only what is clearly visible. If unsure, leave it "
           "out or give a low confidence. Never guess anything you cannot see. Boxes use box_2d = [ymin, xmin, ymax, "
           "xmax] scaled 0-1000 over the whole image.")
_ORTHO = ("Straight-down aerial photo, north at the top. The target building is the one at the exact centre. "
          "Give its roof outline box, the roof type, roof features (solar panels, skylights, roof hatches, HVAC units), "
          "every other building in the image (neighbours) with its box, and site features of the target's lot: "
          "driveway, gates, fences, pool, overhead power lines, parking.")
_OBLIQUE = ("Oblique aerial photo, camera looking {dir}: it shows the {facade}-facing walls. The target building is the "
            "one at the exact centre. Count its above-ground storeys if you can, and list the doors on its walls that "
            "face the camera (front door, garage door, side door, patio door) with boxes.")


def _read(image: Path, prompt: str, schema: type[BaseModel]) -> Optional[BaseModel]:
    block = {"type": "image", "base64": base64.b64encode(image.read_bytes()).decode(), "mime_type": "image/jpeg"}
    return structured("vision", schema, [SystemMessage(_SYSTEM), HumanMessage(content=[{"type": "text", "text": prompt},
                                                                                       block])])


def read_building(building_id: str, force: bool = False, root: Path = RAW_ROOT) -> Optional[dict]:
    """One vision pass per stored image; the result is written next to the plan index. Returns the reading,
    or None when there are no images or no vision model."""
    folder = root / building_id
    images = _json(folder / "vexcel" / "images.json")
    out_path = folder / "derived" / READING_FILE
    if not images or not images.get("images"):
        return None
    if out_path.exists() and not force:
        return _json(out_path)
    if not configured("vision"):
        log.warning("No vision model configured (GEMINI_VISION_MODEL): aerial images of %s not read", building_id)
        return None
    views: dict[str, dict] = {}
    for name, img in images["images"].items():
        path = folder / "vexcel" / img["file"]
        if name == "ortho":
            got = _read(path, _ORTHO, OrthoReading)
        else:
            d = name.removeprefix("oblique-")
            got = _read(path, _OBLIQUE.format(dir=d, facade=OPPOSITE[d]), ObliqueReading)
        if got is None:
            log.warning("Vision could not read %s %s", building_id, name)
            continue
        views[name] = got.model_dump()
    reading = {"building_id": building_id, "model": model_name("vision"), "prompt_version": PROMPT_VERSION,
               "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "views": views}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(reading, indent=1))
    return reading


# ---------- geometry (no LLM) ----------

def _json(path: Path) -> Optional[dict]:
    return json.loads(path.read_text()) if path.is_file() else None


def to_metres(box: Box, side_m: float) -> tuple[float, float, float, float]:
    """box_2d -> (west, south, east, north) in metres from the image centre (east and north positive)."""
    ymin, xmin, ymax, xmax = box
    return ((xmin / 1000 - 0.5) * side_m, (0.5 - ymax / 1000) * side_m,
            (xmax / 1000 - 0.5) * side_m, (0.5 - ymin / 1000) * side_m)


def gap_m(a: tuple, b: tuple) -> float:
    """Shortest distance between two axis-aligned rectangles (0 when they touch or overlap)."""
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return math.hypot(dx, dy)


def side_of(a: tuple, b: tuple) -> str:
    """Compass direction from the centre of a to the centre of b (8 sectors)."""
    dx = (b[0] + b[2]) / 2 - (a[0] + a[2]) / 2
    dy = (b[1] + b[3]) / 2 - (a[1] + a[3]) / 2
    sectors = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]
    return sectors[round(math.degrees(math.atan2(dy, dx)) / 45) % 8]


def lat_lon(box: Box, side_m: float, lat: float, lon: float) -> dict:
    w, s, e, n = to_metres(box, side_m)
    x, y = (w + e) / 2, (s + n) / 2
    return {"lat": round(lat + y / M_PER_DEG_LAT, 7),
            "lon": round(lon + x / (M_PER_DEG_LAT * math.cos(math.radians(lat))), 7)}


def _half(x: float) -> float:
    return round(x * 2) / 2


def stories_seen(obliques: dict[str, dict]) -> Optional[dict]:
    """Storeys of the building: the highest count that two or more oblique views agree on (a lower count from
    another side is a lower wing, e.g. a one-storey garage). {value, views, confidence}, or None.
    A single counted view is taken at reduced confidence; one-off counts among several views are not."""
    counted = {v: r["stories"] for v, r in obliques.items()
               if r.get("target_visible") and r.get("stories") and r.get("stories_confidence", 0) >= MIN_CONFIDENCE}
    if not counted:
        return None
    agreed = [s for s, n in Counter(counted.values()).items() if n >= 2]
    if not agreed and len(counted) > 1:
        return None
    value = max(agreed) if agreed else next(iter(counted.values()))
    views = sorted(v for v, s in counted.items() if s == value)
    conf = min(obliques[v]["stories_confidence"] for v in views) * (1 if len(views) >= 2 else 0.6)
    return {"value": value, "views": views, "confidence": round(min(conf, 0.8), 2)}


# ---------- reading -> provider records (no LLM) ----------

def _ev_id(view: str) -> str:
    return "ev_vx_" + view.replace("-", "_")


ROOF_LABEL = {"solar_panels": "Solar panels on the roof (energized in daylight)", "skylight": "Skylight on the roof",
              "roof_hatch": "Roof hatch", "hvac_unit": "HVAC unit on the roof"}
SITE_LABEL = {"driveway": "Driveway", "gate": "Gate", "fence": "Fence", "pool": "Pool (possible drafting water)",
              "power_line": "Overhead power line", "parking": "Parking area"}


def records(building_id: str, metadata: dict, root: Path = RAW_ROOT) -> Optional[dict[str, dict]]:
    """{view: {markers, exposures, exterior_entries, facts, analysed}} from the stored reading, or None when the
    images were never read. Markers / exposures / facts cite that view's evidence (added by the caller)."""
    reading = _json(root / building_id / "derived" / READING_FILE)
    images = (_json(root / building_id / "vexcel" / "images.json") or {}).get("images", {})
    if not reading:
        return None
    lat, lon = metadata["location"]["lat"], metadata["location"]["lon"]
    out: dict[str, dict] = {}
    for view, r in reading["views"].items():
        out[view] = {"markers": [], "exposures": [], "exterior_entries": [], "facts": {},
                     "analysed": {"file": images.get(view, {}).get("file"), "model": reading.get("model"),
                                  "read_at": reading.get("read_at")}}
    if (o := reading["views"].get("ortho")) is not None:
        _ortho(out["ortho"], o, float(images["ortho"]["bbox_meters"]), lat, lon)
    obliques = {v: r for v, r in reading["views"].items() if v.startswith("oblique-")}
    for view, r in obliques.items():
        _entries(out[view], view, r)
    if seen := stories_seen(obliques):
        first = seen["views"][0]
        out[first]["facts"]["building.stories_aerial"] = {
            "key": "building.stories_aerial", "value": seen["value"], "status": "inferred",
            "confidence": seen["confidence"], "evidence_ids": [_ev_id(v) for v in seen["views"]]}
    return out


def _touches_edge(box: Box, margin: int = 5) -> bool:
    return min(box) <= margin or max(box) >= 1000 - margin


def _ortho(rec: dict, o: dict, side_m: float, lat: float, lon: float) -> None:
    keep = lambda xs: [x for x in xs if x["confidence"] >= MIN_CONFIDENCE]  # noqa: E731
    by_kind: dict[str, list[dict]] = {}
    for f in keep(o.get("roof_features", [])):
        by_kind.setdefault(f["kind"], []).append(f)
    for i, (kind, fs) in enumerate(by_kind.items()):  # repeated roof features are one marker, counted here
        what = ROOF_LABEL.get(kind, fs[0]["label"])
        if len(fs) > 1:
            what = f"{len(fs)} x {what}"
        rec["markers"].append({"id": f"m_vx_roof_{i}", "kind": "roof_access" if kind in ("roof_hatch", "skylight")
                               else "other", "label": f"{what} (aerial: {fs[0]['label']})",
                               "map_position": lat_lon(fs[0]["box_2d"], side_m, lat, lon), "status": "inferred",
                               "confidence": round(min(min(f["confidence"] for f in fs), GEOMETRY_CONFIDENCE_CAP), 2)})
    for i, f in enumerate(keep(o.get("site", []))):
        rec["markers"].append({"id": f"m_vx_site_{i}", "kind": "other",
                               "label": f"{SITE_LABEL.get(f['kind'], 'Site feature')} (aerial: {f['label']})",
                               "map_position": lat_lon(f["box_2d"], side_m, lat, lon), "status": "inferred",
                               "confidence": round(min(f["confidence"], GEOMETRY_CONFIDENCE_CAP), 2)})
    if o.get("roof") in ("flat", "pitched", "mixed") and o.get("target_found"):
        rec["facts"]["building.roof_aerial"] = {"key": "building.roof_aerial", "value": o["roof"], "status": "inferred",
                                                "confidence": round(min(o.get("target_confidence", 0), 0.7), 2),
                                                "evidence_ids": [_ev_id("ortho")]}
    if not (o.get("target_found") and o.get("target") and o.get("target_confidence", 0) >= MIN_CONFIDENCE):
        return  # no target outline: nothing to measure from
    target = to_metres(o["target"], side_m)
    w, s, e, n = target
    if not _touches_edge(o["target"]):  # an outline cut by the image edge is not the building's size
        rec["facts"]["building.footprint_aerial_m"] = {
            "key": "building.footprint_aerial_m", "value": [_half(e - w), _half(n - s)], "status": "inferred",
            "confidence": round(min(o["target_confidence"], GEOMETRY_CONFIDENCE_CAP), 2),
            "evidence_ids": [_ev_id("ortho")]}
    for i, nb in enumerate(keep(o.get("neighbours", []))):
        box = to_metres(nb["box_2d"], side_m)
        d = _half(gap_m(target, box))
        if d > EXPOSURE_MAX_M:
            continue
        side = side_of(target, box)
        kind = nb["kind"].replace("_", " ")
        rec["exposures"].append({"id": f"x_vx_{i}", "label": f"{kind.capitalize()} to the {side}, about {d:g} m away "
                                 f"(aerial: {nb['label']})", "side": side, "distance_m": d,
                                 "map_position": lat_lon(nb["box_2d"], side_m, lat, lon), "status": "inferred",
                                 "confidence": round(min(nb["confidence"], o["target_confidence"],
                                                         GEOMETRY_CONFIDENCE_CAP), 2),
                                 "method": "gap between roof outlines on the north-up ortho, in code"})


def _entries(rec: dict, view: str, r: dict) -> None:
    if not r.get("target_visible"):
        return
    camera = view.removeprefix("oblique-")
    facade = OPPOSITE[camera]
    for i, e in enumerate(x for x in r.get("entries", []) if x["confidence"] >= MIN_CONFIDENCE):
        what = e["kind"].replace("_", " ")
        rec["exterior_entries"].append({"id": f"m_vx_{camera}_entry_{i}", "kind": "entry", "floor": 1,
                                        "label": f"{what.capitalize()} on the {facade} side (aerial, camera facing "
                                                 f"{camera}: {e['label']})",
                                        "status": "inferred",
                                        "confidence": round(min(e["confidence"], ENTRY_CONFIDENCE_CAP), 2)})
