"""Building dossier assembly: three data layers, merged in code. No LLM.

Each layer (costar, matterport, vexcel) is pulled through its provider adapter
(providers.py) and normalized into a LayerData whose evidence is tagged with
that layer as its `source`. merge_layers() then builds the BuildingDossier.
The layer subagents in orchestrator.py call pull_layer(); load_dossier() does
the same pulls directly, for tests and for the facts-only fallback.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Literal, Optional

from pydantic import BaseModel

from flamescout_schemas import BuildingDossier, Evidence, Fact, Marker

from .providers import Providers, get_providers

Layer = Literal["costar", "matterport", "vexcel"]
LAYERS: tuple[Layer, ...] = ("costar", "matterport", "vexcel")
AERIAL_RADIUS_M = 150


class LayerData(BaseModel):
    """One provider's contribution to a dossier, in our own normalized shape."""
    layer: Layer
    building_id: str
    facts: dict[str, Fact] = {}
    tenants: list[Fact] = []
    markers: list[Marker] = []
    exposures: list[dict] = []
    evidence: dict[str, Evidence] = {}
    address: Optional[str] = None                # costar
    coordinates: Optional[dict] = None           # costar
    exterior_entries: bool = False               # vexcel: entries read from aerial obliques are included
    scan_available: bool = False                 # matterport
    scan_captured_at: Optional[datetime] = None  # matterport
    matterport_model_id: Optional[str] = None    # matterport


def _cited(d: LayerData | BuildingDossier) -> list[str]:
    cited: list[str] = []
    for f in [*d.facts.values(), *d.tenants, *d.markers]:
        cited += f.evidence_ids
    for x in d.exposures:
        cited += x.get("evidence_ids", [])
    return cited


def unresolved_evidence(d: LayerData | BuildingDossier) -> list[str]:
    """Evidence ids cited by a fact / tenant / marker / exposure but missing from d.evidence."""
    return sorted({e for e in _cited(d) if e not in d.evidence})


def _merge_markers(markers: Iterable[Marker]) -> list[Marker]:
    """One marker per id: evidence is unioned and the lowest confidence kept."""
    out: dict[str, Marker] = {}
    for m in markers:
        if m.id not in out:
            out[m.id] = m.model_copy(deep=True)
            continue
        kept = out[m.id]
        kept.evidence_ids = list(dict.fromkeys([*kept.evidence_ids, *m.evidence_ids]))
        kept.confidence = min(kept.confidence, m.confidence)
    return list(out.values())


def _checked(layer: LayerData) -> LayerData:
    if wrong := sorted(e.id for e in layer.evidence.values() if e.source != layer.layer):
        raise ValueError(f"{layer.layer} layer for {layer.building_id} has evidence from another source: {wrong}")
    if missing := unresolved_evidence(layer):
        raise ValueError(f"{layer.layer} layer for {layer.building_id} cites evidence it does not carry: {missing}")
    return layer


def _from_records(layer: Layer, building_id: str, records: list[dict],
                  marker_keys: tuple[str, ...] = ("markers",), **extra) -> LayerData:
    """Records are {evidence, markers?, exposures?}: everything in a record cites that record's evidence."""
    evidence: dict[str, Evidence] = {}
    markers: list[Marker] = []
    exposures: list[dict] = []
    for r in records:
        ev = Evidence.model_validate(r["evidence"])
        evidence[ev.id] = ev
        markers += [Marker.model_validate({**m, "evidence_ids": [ev.id]})
                    for key in marker_keys for m in r.get(key, [])]
        exposures += [{**x, "evidence_ids": [ev.id]} for x in r.get("exposures", [])]
    return _checked(LayerData(layer=layer, building_id=building_id, markers=_merge_markers(markers),
                              exposures=exposures, evidence=evidence, **extra))


def pull_costar(building_id: str, providers: Optional[Providers] = None) -> LayerData:
    p = (providers or get_providers()).costar.get_property(building_id)
    return _checked(LayerData(layer="costar", building_id=building_id, address=p["address"],
                              coordinates=p["coordinates"], facts=p.get("facts", {}),
                              tenants=p.get("tenants", []), evidence=p.get("evidence", {})))


def pull_matterport(building_id: str, providers: Optional[Providers] = None) -> LayerData:
    matterport = (providers or get_providers()).matterport
    model = matterport.find_model({"property_id": building_id})
    if not model:  # no scan for this building (PRD FR-10)
        return LayerData(layer="matterport", building_id=building_id)
    model_id = model["model_id"]
    records = [*matterport.get_floorplans(model_id), *matterport.get_frames(model_id, None)]
    return _from_records("matterport", building_id, records, scan_available=True,
                         scan_captured_at=model.get("captured_at"),
                         # fixture model ids are not real Matterport models: nothing for the UI to embed
                         matterport_model_id=None if model_id.startswith("fixture-") else model_id)


def pull_vexcel(building_id: str, providers: Optional[Providers] = None,
                exterior_entries: bool = False) -> LayerData:
    """exterior_entries=True also returns entries read from aerial obliques: low confidence, only
    worth having when there is no interior scan to take entries from."""
    providers = providers or get_providers()
    at = providers.costar.get_property(building_id)["coordinates"]
    keys = ("markers", "exterior_entries") if exterior_entries else ("markers",)
    return _from_records("vexcel", building_id,
                         providers.vexcel.get_aerial(at["lat"], at["lon"], AERIAL_RADIUS_M),
                         marker_keys=keys, exterior_entries=exterior_entries)


_PULL = {"costar": pull_costar, "matterport": pull_matterport, "vexcel": pull_vexcel}


def pull_layer(layer: Layer, building_id: str, providers: Optional[Providers] = None, **options) -> LayerData:
    return _PULL[layer](building_id, providers, **options)


def merge_layers(building_id: str, layers: Iterable[LayerData]) -> BuildingDossier:
    by_layer = {layer.layer: layer for layer in layers}
    if "costar" not in by_layer:
        raise ValueError(f"Cannot build a dossier for {building_id} without the costar layer (address)")
    costar, scan = by_layer["costar"], by_layer.get("matterport")
    evidence: dict[str, Evidence] = {}
    facts: dict[str, Fact] = {}
    for layer in by_layer.values():
        if layer.building_id != building_id:
            raise ValueError(f"{layer.layer} layer is for {layer.building_id!r}, not {building_id!r}")
        if clash := sorted(set(evidence) & set(layer.evidence)):
            raise ValueError(f"Evidence ids used by more than one layer of {building_id}: {clash}")
        evidence.update(layer.evidence)
        facts.update(layer.facts)
    dossier = BuildingDossier(
        building_id=building_id, address=costar.address or "", coordinates=costar.coordinates or {},
        facts=facts, tenants=[t for layer in by_layer.values() for t in layer.tenants],
        markers=_merge_markers(m for layer in by_layer.values() for m in layer.markers),
        exposures=[x for layer in by_layer.values() for x in layer.exposures],
        evidence=evidence,
        scan_available=bool(scan and scan.scan_available),
        scan_captured_at=scan.scan_captured_at if scan else None,
        matterport_model_id=scan.matterport_model_id if scan else None,
    )
    if missing := unresolved_evidence(dossier):
        raise ValueError(f"Dossier for {building_id} cites evidence that is not in its evidence dict: {missing}")
    return dossier


def load_dossier(building_id: str, providers: Optional[Providers] = None) -> BuildingDossier:
    """Pull all three layers and merge them, without any agent."""
    providers = providers or get_providers()
    layers = {layer: pull_layer(layer, building_id, providers) for layer in LAYERS}
    if not layers["matterport"].scan_available:  # no interior scan: take entries from the air instead
        layers["vexcel"] = pull_vexcel(building_id, providers, exterior_entries=True)
    return merge_layers(building_id, layers.values())


def digest(layer: LayerData) -> str:
    """Compact text view for a layer subagent. Every line keeps its evidence ids."""
    def ev(ids: list[str]) -> str:
        return f"[{', '.join(ids)}]" if ids else "[no evidence]"

    lines = [f"{layer.layer} layer for building {layer.building_id}"]
    if layer.address:
        lines.append(f"Address: {layer.address}")
    if layer.layer == "matterport":
        lines.append("Interior scan: " + (f"available, captured {layer.scan_captured_at.date()}"
                                          if layer.scan_available and layer.scan_captured_at else "not available"))
    lines += [f"- {f.key} = {f.value!r} ({f.status}, conf {f.confidence}) {ev(f.evidence_ids)}"
              for f in [*layer.facts.values(), *layer.tenants]]
    lines += [f"- marker {m.id} ({m.kind}): {m.label} ({m.status}, conf {m.confidence}) {ev(m.evidence_ids)}"
              for m in layer.markers]
    lines += [f"- exposure: {x.get('label')} at {x.get('distance_m')} m ({x.get('status')}) "
              f"{ev(x.get('evidence_ids', []))}" for x in layer.exposures]
    return "\n".join(lines)
