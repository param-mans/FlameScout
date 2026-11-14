"""CoStar hackathon kit -> FlameScout's normalized CoStar shape (see providers.py).

The kit (costar-hackathon-kit/) is a static export: data/<dataset>.jsonl plus
schemas/enums.json. It is ~400 MB, licensed, and the repo is public, so it is
NOT committed. `scripts/build_costar_index.py` streams it once into a compact
index (data/costar/properties.jsonl, gitignored) of normalized records:

    {"property_id", "name", "aliases", "address", "lat", "lon", "use", "stories",
     "height_ft"?, "year_built"?, "year_renovated"?, "area_sqft"?, "construction"?,
     "sprinklered"?, "sprinkler_type"?, "units"?, "num_buildings"?, "elevators"?,
     "walkup"?, "construction_status", "as_of", "as_of_basis", "record_uri",
     "photo_uri"?, "tenants": [{"name", "category"?, "floor"?}], "tenants_total",
     "matterport_model_id"?, "matterport_model_ids"}

Field sources are documented in the kit's models/*.md. Unknown enum values
(0 / "Unknown") and nulls are dropped, so they surface as gaps, never guesses.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

MATTERPORT_REF_TYPES = {36, 234}          # ExternalReferenceType: MatterportScan, MatterportStreamingKey
EVENT_COMPLETE, EVENT_RENOVATED = 2, 4    # ConstructionEventType: ConstructionComplete, RenovationFinished
FIRST_FLOOR, SECOND_FLOOR, THIRD_FLOOR, LAST_NUMBERED = 24, 25, 27, 149   # FloorName codes
ELEVATOR_EXISTS = 1                        # RecordStateType: Yes
ELEVATOR_PEOPLE = {"Passenger", "Freight"}
BUSINESS_GONE = {"OutOfBusiness", "PendingDelete"}
MAX_TENANTS = 60


class Enums:
    """Integer enum -> name, from the kit's schemas/enums.json."""

    def __init__(self, table: dict[str, dict[str, str]]):
        self.t = table

    @classmethod
    def load(cls, kit: Path) -> "Enums":
        return cls(json.loads((kit / "schemas" / "enums.json").read_text()))

    def name(self, enum: str, code: Any) -> Optional[str]:
        if code is None:
            return None
        n = self.t.get(enum, {}).get(str(code))
        return None if n is None or "Unknown" in n else n


def humanize(name: str) -> str:
    """'ReinforcedConcrete' -> 'Reinforced concrete'; 'RAndD' -> 'R and d'."""
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).split()
    return " ".join([words[0], *(w.lower() for w in words[1:])]) if words else name


def use_label(e: Enums, ptype: Any, subtype: Any) -> Optional[str]:
    """'Multifamily - Apartments', 'General retail - Auto repair', 'Office'."""
    t, s = e.name("PropertyType", ptype), e.name("PropertySubtype", subtype)
    tl = humanize(t) if t else None
    if s:
        sub = s.split("_", 1)[1] if "_" in s else s
        sl = humanize(sub)
        return f"{tl} - {sl}" if tl and sl.lower() != tl.lower() else (tl or sl)
    return tl


def format_address(a: Optional[dict]) -> str:
    if not a:
        return ""
    num = a.get("streetNumber") or ""
    if a.get("streetNumberEndRange"):
        num = f"{num}-{a['streetNumberEndRange']}"
    street = " ".join(x for x in (num, a.get("streetDirection"), a.get("streetName"),
                                  a.get("streetType"), a.get("streetSuffix")) if x)
    zip5 = (a.get("postcode") or "")[:5]
    region = " ".join(x for x in (a.get("state"), zip5) if x)
    return ", ".join(x for x in (street, a.get("city"), region) if x)


def floor_label(e: Enums, code: Any) -> Optional[int | str]:
    """FloorName code -> floor number (1, 2, 3 ...) or a label ('Basement', 'Penthouse')."""
    if code == FIRST_FLOOR:
        return 1
    if code == SECOND_FLOOR:
        return 2
    if isinstance(code, int) and THIRD_FLOOR <= code <= LAST_NUMBERED:
        return code - 24
    n = e.name("FloorName", code)
    return humanize(n) if n else None


def _subtype_block(rec: dict) -> dict:
    for k in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex",
              "propertyApartment", "propertyHospitality"):
        if rec.get(k):
            return rec[k]
    return {}


def _elevators(e: Enums, rec: dict) -> Optional[int]:
    blk = _subtype_block(rec)
    rows = blk.get("elevators") or (blk.get("structural") or {}).get("elevators") or []
    counts = [r.get("numElevators") for r in rows
              if r.get("elevatorState") == ELEVATOR_EXISTS
              and e.name("ElevatorType", r.get("elevatorType")) in ELEVATOR_PEOPLE]
    known = [c for c in counts if isinstance(c, int)]
    return sum(known) if known else None


def matterport_ids(rec: dict) -> list[str]:
    out: list[str] = []
    for a in rec.get("attachments") or []:
        if a.get("videoExternalReferenceType") in MATTERPORT_REF_TYPES and a.get("videoKey"):
            if a["videoKey"] not in out:
                out.append(a["videoKey"])
    return out


def normalize_property(rec: dict, e: Enums, as_of: str, model_ids: Iterable[str] = (),
                       tenants: Optional[list[dict]] = None) -> dict:
    addr = rec.get("primaryAddress") or {}
    blk = _subtype_block(rec)
    apt, hotel = rec.get("propertyApartment") or {}, rec.get("propertyHospitality") or {}
    events = rec.get("constructionEvents") or []
    built = [ev["year"] for ev in events if ev.get("eventType") == EVENT_COMPLETE and ev.get("year")]
    renov = [ev["year"] for ev in events if ev.get("eventType") == EVENT_RENOVATED and ev.get("year")]
    sprinkler_type = e.name("SprinklerType", rec.get("sprinklerType"))
    sprinklered = rec.get("hasSprinklers")
    if sprinklered is None and sprinkler_type:     # a recorded system type implies a system
        sprinklered = True
    material = e.name("BuildingConstructionMaterialType", rec.get("buildingConstructionMaterial"))
    ids = list(dict.fromkeys([*model_ids, *matterport_ids(rec)]))
    photo = (rec.get("primaryPhoto") or {}).get("attachmentUri")
    tenants = tenants or []
    pid = str(rec["id"])
    out: dict[str, Any] = {
        "property_id": pid,
        "name": rec.get("buildingName") or addr.get("buildingName") or format_address(addr).split(",")[0],
        "aliases": [a for a in (rec.get("aliases") or []) if a],
        "address": format_address(addr),
        "lat": addr.get("latitude"), "lon": addr.get("longitude"),
        "use": use_label(e, rec.get("propertyType"), rec.get("propertySubtype")),
        "stories": rec.get("numStoriesAboveGrade"),
        "height_ft": rec.get("buildingHeightInFeet"),
        "year_built": min(built) if built else None,
        "year_renovated": max(renov) if renov else None,
        "area_sqft": rec.get("grossBuildingAreaSquareFeet") or rec.get("rentableBuildingAreaSquareFeet"),
        "construction": humanize(material) if material else None,
        "sprinklered": sprinklered,
        "sprinkler_type": sprinkler_type,
        "units": apt.get("totalNumUnits") or hotel.get("numUnits"),
        "num_buildings": apt.get("numBuildings"),
        "elevators": _elevators(e, rec),
        "walkup": apt.get("isWalkup"),
        "construction_status": humanize(s) if (s := e.name("ConstructionStatus", rec.get("constructionStatus"))) else None,
        "as_of": as_of, "as_of_basis": "costar kit snapshot (values are as of CoStar's last research touch)",
        "record_uri": f"costar-kit://commercial-property/{pid}",
        "photo_uri": photo,
        "tenants": tenants[:MAX_TENANTS], "tenants_total": len(tenants),
        "matterport_model_id": ids[0] if ids else None,
        "matterport_model_ids": ids,
    }
    if blk.get("rentableBuildingAreaSquareFeet") and not out["area_sqft"]:
        out["area_sqft"] = blk["rentableBuildingAreaSquareFeet"]
    return {k: v for k, v in out.items() if v is not None}


# ---------------- streaming build ----------------

def _jsonl(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _model_ids_from_csv(kit: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    with (kit / "commercial-properties.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("MatterportModelId"):
                out[row["PropertyId"]].append(row["MatterportModelId"])
    return out


def _floor_index(e: Enums, kit: Path) -> dict[int, Any]:
    """propertyFloorId -> floor label, over every property (one cheap pass)."""
    out: dict[int, Any] = {}
    for rec in _jsonl(kit / "data" / "commercial-property.jsonl"):
        for fl in rec.get("floors") or []:
            if fl.get("propertyFloorId") is not None:
                out[fl["propertyFloorId"]] = floor_label(e, fl.get("floorName"))
    return out


def current_tenants(e: Enums, kit: Path) -> dict[str, list[dict]]:
    """Current tenancies (toDate null, known tenant) per PropertyId, largest first."""
    floors = _floor_index(e, kit)
    occ: dict[str, list[tuple[int, float, Any]]] = defaultdict(list)
    for o in _jsonl(kit / "data" / "location-occupancy.jsonl"):
        if o.get("toDate") is not None or o.get("tenantLocationId") is None:
            continue
        spaces = o.get("spaces") or []
        sqft = sum(s.get("squareFeetOccupied") or 0 for s in spaces)
        fl = sorted({floors[s["propertyFloorId"]] for s in spaces
                     if floors.get(s.get("propertyFloorId")) is not None},
                    key=lambda x: (isinstance(x, str), x if isinstance(x, int) else 0, str(x)))
        occ[str(o["propertyId"])].append((o["tenantLocationId"], sqft, fl))
    wanted = {t for rows in occ.values() for t, _, _ in rows}
    companies: dict[int, dict] = {}
    for c in _jsonl(kit / "data" / "company-detail.jsonl"):
        if c["id"] in wanted:
            companies[c["id"]] = c
    out: dict[str, list[dict]] = {}
    for pid, rows in occ.items():
        seen, ts = set(), []
        for tid, sqft, fl in sorted(rows, key=lambda r: -r[1]):
            c = companies.get(tid)
            if not c or not c.get("name") or tid in seen:
                continue
            if e.name("LocationBusinessStatus", c.get("businessStatus")) in BUSINESS_GONE:
                continue
            seen.add(tid)
            ind = e.name("IndustryType", (c.get("profile") or {}).get("industryType"))
            t: dict[str, Any] = {"name": c["name"].strip()}
            if ind:
                t["category"] = humanize(ind).replace(" or ", " / ")
            if fl:
                t["floor"] = fl[0] if len(fl) == 1 else fl
            ts.append(t)
        out[pid] = ts
    return out


def build_index(kit: Path, out_path: Path, as_of: str, with_tenants: bool = True) -> int:
    e = Enums.load(kit)
    models = _model_ids_from_csv(kit)
    tenants = current_tenants(e, kit) if with_tenants else {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for rec in _jsonl(kit / "data" / "commercial-property.jsonl"):
            pid = str(rec["id"])
            f.write(json.dumps(normalize_property(rec, e, as_of, models.get(pid, []), tenants.get(pid))) + "\n")
            n += 1
    tmp.replace(out_path)
    return n
