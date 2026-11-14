"""Dossier assembly (PRD FR-4): CoStar -> (Matterport || Vexcel) -> rules -> BuildingDossier.

Every Fact / Marker / exposure carries evidence ids that resolve in
dossier.evidence. Fields absent from the records are simply absent (the brief
lists them as gaps). Distances come from coordinates (rules.haversine_m), never
from a model. This is a plain function the deep-agent dossier subagent can call.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from flamescout_schemas import BuildingDossier, Evidence, Fact, Marker

from .tools import rules, vision
from .tools.providers import Providers

log = logging.getLogger("flamescout.dossier")

Progress = Callable[[str, dict], None]

# CoStar normalized field -> fact key
COSTAR_FIELDS = {
    "name": "building.name", "use": "building.use", "stories": "building.stories",
    "year_built": "building.year_built", "area_sqft": "building.area_sqft",
    "construction": "building.construction", "sprinklered": "building.sprinklered",
}
MARKER_KINDS = {"entry", "stair", "exit", "elevator", "key_box", "roof_access", "fdc", "standpipe", "other"}


def _dt(v: Any) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _noop(_stage: str, _data: dict) -> None:
    pass


def build_dossier(building_id: str, providers: Providers, cfg: dict,
                  vision_cache: Path, progress: Progress = _noop) -> BuildingDossier:
    progress("costar", {"state": "started"})
    prop = providers.costar.get_property(building_id)
    as_of = _dt(prop.get("as_of"))
    ev_costar = Evidence(id=f"ev_costar_{building_id}", kind="costar_record", source="costar",
                         uri=prop.get("record_uri"), captured_at=as_of,
                         locator={"property_id": prop.get("property_id", building_id)})
    evidence: dict[str, Evidence] = {ev_costar.id: ev_costar}
    rc = cfg["record_confidence"]
    facts: dict[str, Fact] = {
        key: Fact(key=key, value=prop[f], status="verified", confidence=rc,
                  evidence_ids=[ev_costar.id], as_of=as_of)
        for f, key in COSTAR_FIELDS.items() if prop.get(f) is not None
    }
    tenants = [Fact(key="tenant", value={"name": t.get("name"), "category": t.get("category")},
                    status="verified", confidence=rc, evidence_ids=[ev_costar.id], as_of=as_of)
               for t in prop.get("tenants", []) if t.get("name")]
    progress("costar", {"state": "done", "fields": sorted(facts)})

    lat, lon = prop.get("lat"), prop.get("lon")
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan_f = pool.submit(_pull_scan, prop, providers, vision_cache, progress)
        aerial_f = pool.submit(_pull_aerial, lat, lon, providers, cfg, vision_cache, progress)
        scan, (aerial_markers, exposures, aerial_ev) = scan_f.result(), aerial_f.result()

    model, scan_markers, scan_ev = scan
    evidence.update(scan_ev)
    evidence.update(aerial_ev)
    markers = scan_markers + aerial_markers
    scan_available = model is not None

    # ---- deterministic rules ----
    if height := rules.r01_height(facts.get("building.stories"), cfg):
        facts[height[0].key], evidence[height[1].id] = height
    reach, ev = rules.r02_reach(facts.get("building.height_est_m"), cfg)
    facts[reach.key], evidence[ev.id] = reach, ev
    egress, ev = rules.r05_egress(markers, scan_available, cfg)
    facts[egress.key], evidence[ev.id] = egress, ev
    if load := rules.r07_occupant_load(facts.get("building.area_sqft"), facts.get("building.use"), cfg):
        facts[load[0].key], evidence[load[1].id] = load
    progress("rules", {"state": "done"})

    return BuildingDossier(
        building_id=building_id, address=prop.get("address", ""),
        coordinates={"lat": lat, "lon": lon} if lat is not None else {},
        facts=facts, tenants=tenants, markers=markers, exposures=exposures, evidence=evidence,
        scan_available=scan_available,
        scan_captured_at=_dt(model.get("captured_at")) if model else None,
        matterport_model_id=model.get("model_id") if model else prop.get("matterport_model_id"))


def _detections(item: dict, context: vision.Context, cache: Path) -> list[dict]:
    if item.get("detections") is not None:
        return item["detections"]
    return vision.detect(item.get("uri"), context, cache) or []


def _pull_scan(prop: dict, providers: Providers, cache: Path, progress: Progress):
    progress("matterport", {"state": "started"})
    model = providers.matterport.find_model(prop)
    if model is None:
        progress("matterport", {"state": "done", "scan_available": False})
        return None, [], {}
    captured = _dt(model.get("captured_at"))
    markers: list[Marker] = []
    evidence: dict[str, Evidence] = {}
    items = [(f, "scan_frame") for f in providers.matterport.get_frames(model["model_id"], None)]
    items += [(p, "scan_floorplan") for p in providers.matterport.get_floorplans(model["model_id"])]
    for item, kind in items:
        item_id = item.get("frame_id") or f"plan{item.get('floor', '')}"
        for i, d in enumerate(_detections(item, kind, cache)):  # type: ignore[arg-type]
            if d.get("kind") not in MARKER_KINDS:
                continue
            eid = f"ev_mp_{item_id}_{i}"
            evidence[eid] = Evidence(id=eid, kind=kind, source="matterport", uri=item.get("uri"),  # type: ignore[arg-type]
                                     captured_at=captured,
                                     locator={"model_id": model["model_id"], "item_id": item_id,
                                              "bbox": d.get("bbox"), "detection_source": d.get("source")})
            markers.append(Marker(
                id=f"mk_{item_id}_{i}", kind=d["kind"], label=d.get("label", d["kind"]),
                floor=d.get("floor", item.get("floor")), scan_position=d.get("scan_position"),
                status="verified" if d.get("source") == "annotation" else "inferred",
                confidence=float(d.get("confidence", 0.5)), evidence_ids=[eid]))
    progress("matterport", {"state": "done", "scan_available": True, "markers": len(markers)})
    return model, markers, evidence


def _pull_aerial(lat: Optional[float], lon: Optional[float], providers: Providers, cfg: dict,
                 cache: Path, progress: Progress):
    progress("vexcel", {"state": "started"})
    if lat is None or lon is None:
        progress("vexcel", {"state": "done", "images": 0, "why": "no coordinates"})
        return [], [], {}
    images = providers.vexcel.get_aerial(lat, lon, cfg["aerial_search_radius_m"])
    markers: list[Marker] = []
    exposures: list[dict] = []
    evidence: dict[str, Evidence] = {}
    band_ev = rules.rule_evidence("R-03", [], {"bands_m": cfg["exposure_bands_m"],
                                               "method": "centroid-to-centroid haversine"})
    for img in images:
        img_id = img.get("image_id", "img")
        for i, d in enumerate(_detections(img, "aerial_crop", cache)):
            eid = f"ev_vx_{img_id}_{i}"
            evidence[eid] = Evidence(id=eid, kind="aerial_crop", source="vexcel", uri=img.get("uri"),
                                     captured_at=_dt(img.get("captured_at")),
                                     locator={"image_id": img_id, "bbox": d.get("bbox"),
                                              "detection_source": d.get("source")})
            pos = {"lat": d["lat"], "lon": d["lon"]} if d.get("lat") is not None else None
            status = "verified" if d.get("source") == "annotation" else "inferred"
            if d.get("kind") == "adjacent_structure":
                dist = round(rules.haversine_m(lat, lon, d["lat"], d["lon"]), 1) if pos else None
                evidence[band_ev.id] = band_ev
                exposures.append({"id": f"ex_{img_id}_{i}", "label": d.get("label", "Adjacent structure"),
                                  "distance_m": dist, "band": rules.r03_exposure_band(dist, cfg),
                                  "map_position": pos, "status": "inferred",
                                  "confidence": float(d.get("confidence", 0.5)),
                                  "evidence_ids": [eid, band_ev.id]})
            elif d.get("kind") in MARKER_KINDS:
                markers.append(Marker(id=f"mk_{img_id}_{i}", kind=d["kind"], label=d.get("label", d["kind"]),
                                      map_position=pos, status=status,
                                      confidence=float(d.get("confidence", 0.5)), evidence_ids=[eid]))
    exposures.sort(key=lambda x: (x["distance_m"] is None, x["distance_m"] or 0))
    progress("vexcel", {"state": "done", "images": len(images), "exposures": len(exposures)})
    return markers, exposures, evidence
