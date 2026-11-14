"""Validator for the IncidentOverlay contract (stdlib only).
Usage: python3 validate_overlay.py overlay.json plan_index.json
Exit code 0 = valid, 1 = invalid. Prints every problem found."""
from __future__ import annotations

import json, re, sys
from datetime import datetime

STATUS = {"verified", "inferred", "reported", "unverified", "contradicted"}
LOC_KINDS = {"room", "anchor", "floor", "unknown"}
ANCHOR_POS = {"center", "near_door", "near_window", "near_stairs"}
NEAR_NEEDS_REF = {"near_door": "openings", "near_window": "openings", "near_stairs": "stairs"}
FIRE_KINDS = {"smoke", "fire", "heat"}
HAZARD_KINDS = {"gas", "chemical", "battery", "electrical", "structural", "other"}
OBJ_TYPES = {"rescue", "recon"}
TARGET_KINDS = {"occupant", "occupant_group", "room", "floor"}
WAYPOINT_KINDS = {"room", "opening", "stairs", "exterior"}
FORBIDDEN_KEYS = {"x", "y", "z", "lat", "lon", "latitude", "longitude", "coordinates", "polygon", "points"}
DIRECTIVE = re.compile(r"\b(must|immediately|do it now|ordered|100\s*%|guaranteed)\b", re.I)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def walk_keys(o: object, path: str = ""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "." + k, k
            yield from walk_keys(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk_keys(v, "%s[%d]" % (path, i))


def validate(ov: dict, plan: dict) -> list[str]:
    errs = []
    E = errs.append
    rooms, openings, stairs, floors = (set(plan.get(k, {})) if k in ("rooms", "openings") else set(plan.get(k, [])) for k in ("rooms", "openings", "stairs", "floors"))

    for k in ("incident_id", "model_id", "version", "as_of"):
        if k not in ov:
            E("missing top-level field: " + k)
    if not isinstance(ov.get("version"), int) or ov.get("version", 0) < 1:
        E("version must be an integer >= 1")
    try:
        datetime.fromisoformat(str(ov.get("as_of", "")).replace("Z", "+00:00"))
    except ValueError:
        E("as_of must be an ISO 8601 timestamp")

    for p, k in walk_keys(ov):
        if k in FORBIDDEN_KEYS:
            E("%s: raw geometry or coordinates are not allowed in agent output (use ids and anchors)" % p)

    ids = {}
    def reg(i, where):
        if not i:
            E(where + ": missing id")
        elif i in ids:
            E("%s: duplicate id %r (also used in %s)" % (where, i, ids[i]))
        else:
            ids[i] = where

    def common(e, where):
        reg(e.get("id"), where)
        if e.get("status") not in STATUS:
            E("%s: status must be one of %s" % (where, sorted(STATUS)))
        c = e.get("confidence")
        if not isinstance(c, (int, float)) or not 0 <= c <= 1:
            E(where + ": confidence must be a number between 0 and 1")
        ev = e.get("evidence_ids")
        if not isinstance(ev, list) or (not ev and e.get("status") != "unverified"):
            E(where + ": evidence_ids is required (empty only when status is 'unverified')")
        try:
            datetime.fromisoformat(str(e.get("reported_at", "")).replace("Z", "+00:00"))
        except ValueError:
            E(where + ": reported_at must be an ISO 8601 timestamp")

    def location(loc, where):
        if not isinstance(loc, dict) or loc.get("kind") not in LOC_KINDS:
            E("%s: location.kind must be one of %s" % (where, sorted(LOC_KINDS)))
            return
        k = loc["kind"]
        if k == "room" and loc.get("room_id") not in rooms:
            E("%s: room_id %r does not exist in the plan" % (where, loc.get("room_id")))
        if k == "anchor":
            a = loc.get("anchor") or {}
            if a.get("room_id") not in rooms:
                E("%s: anchor.room_id %r does not exist in the plan" % (where, a.get("room_id")))
            pos = a.get("position")
            if pos not in ANCHOR_POS:
                E("%s: anchor.position must be one of %s" % (where, sorted(ANCHOR_POS)))
            elif pos in NEAR_NEEDS_REF:
                pool = openings if NEAR_NEEDS_REF[pos] == "openings" else stairs
                if a.get("ref_id") not in pool:
                    E("%s: anchor.ref_id %r does not exist (%s)" % (where, a.get("ref_id"), NEAR_NEEDS_REF[pos]))
        if k == "floor":
            if loc.get("floor_id") not in floors and not isinstance(loc.get("floor_reported"), int):
                E("%s: floor location needs a valid floor_id or an integer floor_reported" % where)
        if k == "unknown" and (loc.get("room_id") or loc.get("anchor")):
            E(where + ": kind 'unknown' must not carry a room or anchor")
        if "floor_id" in loc and loc["floor_id"] not in floors:
            E("%s: floor_id %r does not exist in the plan" % (where, loc["floor_id"]))

    for i, o in enumerate(ov.get("occupants", [])):
        w = "occupants[%d]" % i
        common(o, w)
        location(o.get("location"), w)
        n, r = o.get("count"), o.get("count_range")
        if not ((isinstance(n, int) and n >= 1) or (isinstance(r, list) and len(r) == 2 and all(isinstance(v, int) for v in r) and 1 <= r[0] <= r[1])):
            E(w + ": needs count (int >= 1) or count_range [min, max]")
    for i, f in enumerate(ov.get("fire", [])):
        w = "fire[%d]" % i
        common(f, w)
        location(f.get("location"), w)
        if f.get("kind") not in FIRE_KINDS:
            E("%s: kind must be one of %s" % (w, sorted(FIRE_KINDS)))
    for i, h in enumerate(ov.get("hazards", [])):
        w = "hazards[%d]" % i
        common(h, w)
        location(h.get("location"), w)
        if h.get("kind") not in HAZARD_KINDS:
            E("%s: kind must be one of %s" % (w, sorted(HAZARD_KINDS)))
    for i, b in enumerate(ov.get("blocked", [])):
        w = "blocked[%d]" % i
        common(b, w)
        if b.get("ref_id") not in openings | stairs | rooms:
            E("%s: ref_id %r does not exist in the plan" % (w, b.get("ref_id")))

    entities = {e["id"]: e for k in ("occupants", "fire", "hazards", "blocked") for e in ov.get(k, []) if e.get("id")}
    groups = {}
    for e in entities.values():
        if e.get("group_id"):
            groups.setdefault(e["group_id"], []).append(e)
    for g, members in groups.items():
        if len(members) < 2:
            E("group %r has fewer than 2 members" % g)
        if any(m.get("status") != "contradicted" for m in members):
            E("group %r: every member must have status 'contradicted'" % g)
    for i, c in enumerate(ov.get("contradictions", [])):
        g = c.get("group_id")
        if g not in groups:
            E("contradictions[%d]: group_id %r matches no entities" % (i, g))
    for g in groups:
        if g not in {c.get("group_id") for c in ov.get("contradictions", [])}:
            E("group %r has no entry in contradictions[]" % g)

    for i, o in enumerate(ov.get("objectives", [])):
        w = "objectives[%d]" % i
        reg(o.get("id"), w)
        if o.get("type") not in OBJ_TYPES:
            E("%s: type must be one of %s" % (w, sorted(OBJ_TYPES)))
        t = o.get("target") or {}
        if t.get("kind") not in TARGET_KINDS:
            E("%s: target.kind must be one of %s" % (w, sorted(TARGET_KINDS)))
        else:
            ref = t.get("ref_id")
            ok = {"occupant": ref in entities and ref in {e["id"] for e in ov.get("occupants", [])},
                  "occupant_group": ref in groups,
                  "room": ref in rooms,
                  "floor": ref in floors or isinstance(t.get("floor_reported"), int)}[t["kind"]]
            if not ok:
                E("%s: target.ref_id %r does not resolve for kind %r" % (w, ref, t["kind"]))
        if not isinstance(o.get("priority"), int) or o["priority"] < 1:
            E(w + ": priority must be an integer >= 1")
        if not o.get("evidence_ids"):
            E(w + ": evidence_ids is required")

    for i, c in enumerate(ov.get("considerations", [])):
        w = "considerations[%d]" % i
        reg(c.get("id"), w)
        txt = str(c.get("text", ""))
        if not txt.startswith("Consider"):
            E(w + ": text must be advisory and start with 'Consider'")
        if DIRECTIVE.search(txt) or DIRECTIVE.search(str(c.get("rationale", ""))):
            E(w + ": directive wording found (must, immediately, 100%, ordered, guaranteed are not allowed)")
        if not c.get("rationale") or not c.get("evidence_ids"):
            E(w + ": rationale and evidence_ids are required")
        for r in c.get("refs", []):
            if r not in ids and r not in groups:
                E("%s: ref %r matches nothing in this overlay" % (w, r))

    for i, r in enumerate(ov.get("routes", [])):
        w = "routes[%d]" % i
        reg(r.get("id"), w)
        if r.get("produced_by") != "planner":
            E(w + ": routes are produced by the deterministic planner, produced_by must be 'planner'")
        if r.get("objective_id") not in {o.get("id") for o in ov.get("objectives", [])}:
            E("%s: objective_id %r does not exist" % (w, r.get("objective_id")))
        pools = {"room": rooms, "opening": openings, "stairs": stairs, "exterior": openings}
        for j, wp in enumerate(r.get("waypoints", [])):
            if wp.get("kind") not in WAYPOINT_KINDS:
                E("%s.waypoints[%d]: kind must be one of %s" % (w, j, sorted(WAYPOINT_KINDS)))
            elif wp.get("ref_id") not in pools[wp["kind"]]:
                E("%s.waypoints[%d]: ref_id %r does not exist" % (w, j, wp.get("ref_id")))
        if len(r.get("waypoints", [])) < 2:
            E(w + ": at least 2 waypoints")
    return errs


if __name__ == "__main__":
    errs = validate(load(sys.argv[1]), load(sys.argv[2]))
    for e in errs:
        print("ERROR", e)
    print("VALID" if not errs else "%d problem(s)" % len(errs))
    sys.exit(1 if errs else 0)
