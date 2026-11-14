"""Reference resolver: turns a spoken place ("the bedroom over the kitchen") into plan ids.
No orientation is used (no front/back/left/right), only geometry relations stored in the plan index.
It resolves only when exactly ONE room matches. Otherwise it returns the candidates and a clarifying
question, and the caller must keep the location at floor level. Stdlib only."""
from __future__ import annotations


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _targets(index: dict, ref: str) -> set[str]:
    """A relation target can be a room id or a room type (case-insensitive)."""
    rooms = index.get("rooms", {})
    if ref in rooms:
        return {ref}
    return {rid for rid, r in rooms.items() if _norm(r.get("type")) == _norm(ref)}


def resolve_place(index: dict, *, floor_id: str | None = None, type: str | None = None,
                  size: str | None = None, over: str | None = None, under: str | None = None,
                  next_to: str | None = None, at_stairs: bool | None = None) -> dict:
    """size is 'largest' or 'smallest'. over/under/next_to take a room id or a room type.
    Returns {status, ids, question, reason}. status: resolved | ambiguous | none."""
    rooms = index.get("rooms", {})
    cands = []
    for rid, r in rooms.items():
        if floor_id and r.get("floor_id") != floor_id:
            continue
        if type and _norm(r.get("type")) != _norm(type):
            continue
        if at_stairs is not None and bool(r.get("at_stairs")) != at_stairs:
            continue
        ok = True
        for key, ref in (("over", over), ("under", under), ("next_to", next_to)):
            if ref is not None and not (set(r.get(key) or []) & _targets(index, ref)):
                ok = False
        if ok:
            cands.append((rid, r))
    if size and cands:
        if size == "largest":
            cands = [(i, r) for i, r in cands if r.get("size_rank") == 1]
        elif size == "smallest":
            cands = [(i, r) for i, r in cands if r.get("size_rank") == r.get("size_of")]
    ids = sorted(i for i, _ in cands)
    if not ids:
        return {"status": "none", "ids": [], "question": None, "reason": "no room matches"}
    if len(ids) == 1:
        return {"status": "resolved", "ids": ids, "question": None, "reason": None}
    return {"status": "ambiguous", "ids": ids, "question": _question(index, cands),
            "reason": "%d rooms match" % len(ids)}


def _types(index: dict, ids: list[str]) -> set[str]:
    return {_norm(index["rooms"][i].get("type")) for i in ids if i in index.get("rooms", {})}


def _question(index: dict, cands: list) -> str:
    for key, phrase in (("over", "over the"), ("next_to", "next to the")):
        per = [_types(index, r.get(key) or []) for _, r in cands]
        union = sorted(set().union(*per)) if per else []
        if len(union) >= 2 and len({frozenset(p) for p in per}) > 1:
            return "%s %s or %s %s?" % (phrase, union[0], phrase, union[1])
    flags = {bool(r.get("at_stairs")) for _, r in cands}
    if len(flags) > 1:
        return "the one at the stairs or not?"
    if len({r.get("size_rank") for _, r in cands}) > 1:
        return "the larger one or the smaller one?"
    return "which one? (%d candidates)" % len(cands)
