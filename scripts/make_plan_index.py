"""Build the plan index of a building from its plan.json (stdlib only).

The plan index is what the incident agent receives to resolve phrases like "the back bedroom upstairs" into ids
(docs/INCIDENT_OVERLAY_SPEC.md):

    {"floors": [floor ids], "rooms": {id: {floor_id, label, type}}, "openings": {id: {type, exterior, floor_id}},
     "stairs": [stair ids]}

It only reads plan.json: stairs ids and opening exterior flags must already be stored there (they are computed once
with the viewer's logic; see data/raw/FIELD_NOTES.md). Void rooms are left out. Accepted plan.json layouts:

- normalized (data/raw/<id>/matterport/plan.json): floors[{id}], rooms[{id, floor_id, label, type}],
  doors[] / windows[] / dividers[] {id, floor_id, exterior}, stairs[{id}];
- viewer (house viewer API): floors[{id, rooms[{id, label, type}], openings[{id, type, exterior}]}], stairs[{id}].

Usage:
    python scripts/make_plan_index.py data/raw/209217448/matterport/plan.json            # writes ../derived/plan_index.json
    python scripts/make_plan_index.py path/to/plan.json -o out.json                       # explicit output
    python scripts/make_plan_index.py --empty -o data/raw/23296/derived/plan_index.json   # building without a scan
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TypedDict


class RoomEntry(TypedDict):
    floor_id: str
    label: str
    type: str | None


class OpeningEntry(TypedDict):
    type: str
    exterior: bool
    floor_id: str


class PlanIndex(TypedDict):
    floors: list[str]
    rooms: dict[str, RoomEntry]
    openings: dict[str, OpeningEntry]
    stairs: list[str]


OPENING_TYPES = {"doors": "doorway", "windows": "window", "dividers": "divider"}


def empty_index() -> PlanIndex:
    return {"floors": [], "rooms": {}, "openings": {}, "stairs": []}


def _room(r: dict[str, Any], floor_id: str) -> RoomEntry:
    rtype = r.get("type")
    label = (r.get("label") or rtype or "Room").upper()
    return {"floor_id": floor_id, "label": label, "type": rtype.lower() if rtype else None}


def _stairs(plan: dict[str, Any]) -> list[str]:
    stairs = plan.get("stairs")
    if not isinstance(stairs, list) or any(not isinstance(s, dict) or not s.get("id") for s in stairs):
        raise ValueError("plan.json has no stairs[] with an id per flight; compute it first (see data/raw/FIELD_NOTES.md)")
    return [s["id"] for s in stairs]


def _exterior(o: dict[str, Any]) -> bool:
    if not isinstance(o.get("exterior"), bool):
        raise ValueError("opening %r has no exterior flag; compute it first (see data/raw/FIELD_NOTES.md)" % o.get("id"))
    return o["exterior"]


def build_index(plan: dict[str, Any]) -> PlanIndex:
    """Plan index from a plan.json (normalized or viewer layout). Raises ValueError if the stored extras are missing."""
    floors = [f["id"] for f in plan["floors"]]
    rooms: dict[str, RoomEntry] = {}
    openings: dict[str, OpeningEntry] = {}
    if isinstance(plan.get("rooms"), list):  # normalized layout
        for r in plan["rooms"]:
            if r.get("type") != "Void":
                rooms[r["id"]] = _room(r, r["floor_id"])
        for key, otype in OPENING_TYPES.items():
            for o in plan.get(key, []):
                openings[o["id"]] = {"type": otype, "exterior": _exterior(o), "floor_id": o["floor_id"]}
    else:  # viewer layout
        for f in plan["floors"]:
            for r in f.get("rooms", []):
                if r.get("type") != "Void":
                    rooms[r["id"]] = _room(r, f["id"])
            for o in f.get("openings", []):
                openings[o["id"]] = {"type": o["type"], "exterior": _exterior(o), "floor_id": f["id"]}
    return {"floors": floors, "rooms": rooms, "openings": openings, "stairs": _stairs(plan)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plan", nargs="?", help="path to plan.json")
    ap.add_argument("-o", "--output", help="output path (default: ../derived/plan_index.json next to plan.json)")
    ap.add_argument("--empty", action="store_true", help="write the empty index of a building without a scan")
    a = ap.parse_args(argv)
    if a.empty:
        index, out = empty_index(), Path(a.output) if a.output else None
        if out is None:
            ap.error("--empty needs -o")
    else:
        if not a.plan:
            ap.error("plan.json path required")
        src = Path(a.plan)
        index = build_index(json.loads(src.read_text(encoding="utf-8")))
        out = Path(a.output) if a.output else src.parent.parent / "derived" / "plan_index.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("%s: %d floors, %d rooms, %d openings, %d stairs" % (out, len(index["floors"]), len(index["rooms"]), len(index["openings"]), len(index["stairs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
