"""Tests for the reference place resolver. Runs with pytest or plain `python3 tests/test_resolve_place.py`.
The resolver and the example index live in packages/schemas/overlay/. If you move them, change HERE."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "packages" / "schemas" / "overlay"
sys.path.insert(0, str(HERE))

from resolve_place import resolve_place  # noqa: E402

IDX = json.loads((HERE / "plan_index.example.json").read_text(encoding="utf-8"))


def test_bedroom_over_the_kitchen() -> None:
    r = resolve_place(IDX, floor_id="f2", type="bedroom", over="kitchen")
    assert r["status"] == "resolved" and r["ids"] == ["r10"]


def test_bedroom_over_the_living_room() -> None:
    assert resolve_place(IDX, floor_id="f2", type="Bedroom", over="living room")["ids"] == ["r9"]


def test_relation_target_can_be_an_id() -> None:
    assert resolve_place(IDX, type="bedroom", over="r4")["ids"] == ["r10"]


def test_bedroom_at_the_top_of_the_stairs() -> None:
    assert resolve_place(IDX, floor_id="f2", type="bedroom", at_stairs=True)["ids"] == ["r9"]


def test_kitchen_under_a_bedroom() -> None:
    assert resolve_place(IDX, type="kitchen", under="bedroom")["ids"] == ["r4"]


def test_bedroom_next_to_the_staircase() -> None:
    assert resolve_place(IDX, floor_id="f2", type="bedroom", next_to="staircase")["ids"] == ["r9"]


def test_size_resolves() -> None:
    assert resolve_place(IDX, floor_id="f2", type="bedroom", size="largest")["ids"] == ["r10"]
    assert resolve_place(IDX, floor_id="f2", type="bedroom", size="smallest")["ids"] == ["r9"]


def test_two_bedrooms_without_a_relation_are_ambiguous_and_ask() -> None:
    r = resolve_place(IDX, floor_id="f2", type="bedroom")
    assert r["status"] == "ambiguous" and r["ids"] == ["r10", "r9"]
    assert r["question"] == "over the kitchen or over the living room?"


def test_unique_room_needs_no_relation() -> None:
    assert resolve_place(IDX, type="garage")["ids"] == ["r5"]


def test_no_match() -> None:
    assert resolve_place(IDX, floor_id="f1", type="bedroom")["status"] == "none"
    assert resolve_place(IDX, type="bedroom", over="garage")["status"] == "none"


def test_example_relations_are_symmetric() -> None:
    rooms = IDX["rooms"]
    for rid, r in rooms.items():
        for o in r.get("next_to", []):
            assert rid in rooms[o].get("next_to", []), (rid, o)
        for o in r.get("over", []):
            assert rid in rooms[o].get("under", []), (rid, o)
        for o in r.get("under", []):
            assert rid in rooms[o].get("over", []), (rid, o)


def test_example_has_no_orientation_fields() -> None:
    assert "orientation" not in IDX
    assert all("sides" not in r for r in IDX["rooms"].values())



FAIRDALE_LIKE = {"rooms": {
    "b1": {"floor_id": "f1", "type": "bedroom", "size_rank": 1, "size_of": 3, "next_to": ["c1", "lv", "bt", "h1"]},
    "b2": {"floor_id": "f1", "type": "bedroom", "size_rank": 2, "size_of": 3, "next_to": ["c2", "b3", "h1"]},
    "b3": {"floor_id": "f1", "type": "bedroom", "size_rank": 2, "size_of": 3, "next_to": ["b2", "bt", "c3", "h1"]},
    "c1": {"floor_id": "f1", "type": "closet"}, "c2": {"floor_id": "f1", "type": "closet"}, "c3": {"floor_id": "f1", "type": "closet"},
    "lv": {"floor_id": "f1", "type": "living room"}, "bt": {"floor_id": "f1", "type": "bathroom"}, "h1": {"floor_id": "f1", "type": "hallway"},
}}


def test_tied_smallest_is_ambiguous_not_none() -> None:
    r = resolve_place(FAIRDALE_LIKE, type="bedroom", size="smallest")
    assert r["status"] == "ambiguous" and r["ids"] == ["b2", "b3"]


def test_largest_resolves_even_with_ties_below() -> None:
    assert resolve_place(FAIRDALE_LIKE, type="bedroom", size="largest")["ids"] == ["b1"]


def test_question_ignores_own_and_generic_types_and_offers_another() -> None:
    r = resolve_place(FAIRDALE_LIKE, type="bedroom")
    assert r["status"] == "ambiguous"
    assert r["question"] == "next to the bathroom, next to the living room or another one?"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
