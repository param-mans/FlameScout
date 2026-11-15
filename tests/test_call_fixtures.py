"""Call fixtures (data/fixtures/calls/*.json): structure of every scenario, and the new scenarios load the same way as
the existing ones. Offline, no models. Fusion is run in memory (FusionEngine, no workspace) only to check it accepts the
scenarios; for s6-s10, which expected facts the keyword extractor recovers is reported in docs/CALL_FIXTURES.md, not
asserted. Showcases (show_*) and the UI manifest (manifest.json) are asserted strictly, including the keyword facts and
resolve_place on the real plan (skipped without data/raw)."""
import json
import re
from pathlib import Path

import pytest

from flamescout_schemas import CallSegment
from services.agents.extract import KeywordExtractor
from services.agents.fusion import FusionEngine

from overlay.resolve_place import resolve_place

from .conftest import FIXTURES, ROOT, load_script

CALLS = FIXTURES / "calls"
EXPECTED = CALLS / "expected"
NEW = ["s6_fairdale", "s7_fairhope_oak", "s8_playa_lucia", "s9_nwtc", "s10_wilshire"]
SCRIPT_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")  # services/api/main.py SimulateIn.script_id
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MANIFEST = CALLS / "manifest.json"  # UI listing contract, not a script
SCENARIOS = sorted(p for p in CALLS.glob("*.json") if p != MANIFEST)
SHOWCASE = ["show_fairdale", "show_fairhope", "show_playa"]
VIEWER_CATALOG = ROOT / "apps" / "web" / "public" / "house-viewer" / "samples" / "index.json"


def _data(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_structure(path):
    d = _data(path)
    assert set(d) == {"script_id", "description", "segments"}
    assert d["script_id"] == path.stem.upper() and SCRIPT_ID.fullmatch(d["script_id"])
    segs = [CallSegment(**s) for s in d["segments"]]          # speakers: call_taker | caller
    assert segs and any(s.speaker == "caller" for s in segs)
    order = list(dict.fromkeys(s.call_id for s in segs))
    blocks = [k for i, k in enumerate(s.call_id for s in segs) if i == 0 or segs[i - 1].call_id != k]
    assert blocks == order, f"a call id comes back after another call: {blocks}"
    for cid in order:
        call = [s for s in segs if s.call_id == cid]
        for a, b in zip(call, call[1:]):
            assert b.t_start >= a.t_end, f"{cid}: segments overlap or go back in time at {b.t_start}"
        assert all(s.t_end > s.t_start >= 0 for s in call)
    for s in segs:
        assert not PHONE.search(s.text) and not EMAIL.search(s.text), s.text


def test_script_ids_are_unique():
    ids = [_data(p)["script_id"] for p in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_expected_files_are_not_listed_as_scripts():
    # the API lists CALLS/*.json as scripts; expectations live one level down
    assert not any(p.parent == CALLS for p in EXPECTED.glob("*.json"))


@pytest.mark.parametrize("stem", NEW)
def test_new_scenarios(stem):
    d = _data(CALLS / f"{stem}.json")
    calls = {s["call_id"] for s in d["segments"]}
    per_call = [sum(1 for s in d["segments"] if s["call_id"] == c) for c in calls]
    assert all(8 <= n <= 16 for n in per_call), per_call
    assert not any(re.search(r"\b911\b", s["text"]) for s in d["segments"]), "no real emergency numbers"
    # loads exactly like the existing scripts (tests' load_script = the API's _load_script / scripts/run_*.py path)
    assert load_script(d["script_id"]) == [CallSegment(**s) for s in d["segments"]]
    # expectations: same script id, referenced from the description
    exp = _data(EXPECTED / f"{stem}.json")
    assert exp["script_id"] == d["script_id"] and f"calls/expected/{stem}.json" in d["description"]
    assert {"any_extractor", "llm_only", "building"} <= set(exp)
    referenced = {v["call_id"] for v in _walk(exp) if isinstance(v, dict) and "call_id" in v}
    assert referenced <= calls, f"expected file names calls that are not in the script: {referenced - calls}"


@pytest.mark.parametrize("stem", NEW + SHOWCASE)
def test_expected_plan_ids_exist(stem):
    exp = _data(EXPECTED / f"{stem}.json")
    index_path = exp["building"]["plan_index"]
    if index_path is None:
        return
    p = ROOT / index_path
    if not p.exists():
        pytest.skip(f"{index_path} is local only (data/raw is not in git)")
    index = json.loads(p.read_text())
    for v in _walk(exp):
        if isinstance(v, dict):
            if "room_id" in v:
                assert v["room_id"] in index["rooms"], v
            if "floor_id" in v:
                assert v["floor_id"] in index["floors"], v


@pytest.mark.parametrize("stem", NEW + SHOWCASE)
def test_fusion_accepts_the_scenario_in_memory(stem):
    eng = FusionEngine(f"inc_{stem}", extractor=KeywordExtractor())
    rec = eng.ingest_all(load_script(stem))
    assert rec.call_ids == list(dict.fromkeys(s.call_id for s in load_script(stem)))


# ---------- manifest (UI listing) and showcases ----------

def _manifest() -> list[dict]:
    return _data(MANIFEST)["scripts"]


def test_manifest_contract():
    rows = _manifest()
    keys = {"id", "title", "group", "house", "inconsistent", "visible"}
    assert all(set(r) == keys for r in rows), "manifest keys are a contract with the API and the UI"
    assert all(r["group"] in ("showcase", "test") for r in rows)
    assert all(isinstance(r["inconsistent"], bool) and isinstance(r["visible"], bool) for r in rows)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    for sid in ids:  # every id loads as a script
        assert (CALLS / f"{sid.lower()}.json").exists(), sid
        assert load_script(sid)
    groups = [r["group"] for r in rows]
    assert groups == sorted(groups, key=lambda g: g != "showcase"), "showcases come first"
    for r in rows:
        assert r["title"].startswith("SHOWCASE · ") == (r["group"] == "showcase"), r["title"]


def test_manifest_has_exactly_one_visible_inconsistent_script():
    bad = [r for r in _manifest() if r["visible"] and r["inconsistent"]]
    assert len(bad) == 1 and "INCONSISTENT" in bad[0]["title"], bad


def test_manifest_visible_scripts_have_no_unexpected_contradictions():
    # a visible script marked consistent must not expect contradictions at any level
    for r in _manifest():
        exp = EXPECTED / f"{r['id'].lower()}.json"
        if r["visible"] and not r["inconsistent"] and exp.exists():
            assert not _contradictions(_data(exp)), r["id"]


def test_manifest_houses_are_in_the_viewer_catalog():
    catalog = {x["id"] for x in json.loads(VIEWER_CATALOG.read_text())}
    for r in _manifest():
        if r["visible"]:
            assert r["house"] in catalog, r


@pytest.mark.parametrize("stem", SHOWCASE)
def test_showcase_is_short_and_consistent(stem):
    d = _data(CALLS / f"{stem}.json")
    segs = d["segments"]
    assert 2 <= len(segs) <= 3 and len({s["call_id"] for s in segs}) == 1
    assert segs[0]["t_start"] == 0.0 and segs[-1]["t_end"] <= 10.0
    assert sum(len(s["text"].split()) for s in segs) <= 28
    assert not any(re.search(r"\b911\b", s["text"]) for s in segs)
    assert not any(re.search(r"\b(front|back|left|right)\b", s["text"], re.I) for s in segs), "no orientation words"
    row = next(r for r in _manifest() if r["id"] == d["script_id"])
    assert row["group"] == "showcase" and row["visible"] and not row["inconsistent"]
    exp = _data(EXPECTED / f"{stem}.json")
    assert exp["script_id"] == d["script_id"] and f"calls/expected/{stem}.json" in d["description"]
    assert exp["building"]["id"] == row["house"]
    assert not _contradictions(exp)
    # what the keyword extractor must pick up literally, with no contradiction
    rec = FusionEngine(f"inc_{stem}", extractor=KeywordExtractor()).ingest_all(load_script(stem))
    assert not rec.contradictions
    for key, want in exp["any_extractor"]["facts"].items():
        got = rec.facts[key].value
        assert (sorted(got) == sorted(want)) if isinstance(want, list) else got == want, (key, got, want)


@pytest.mark.parametrize("stem", SHOWCASE)
def test_showcase_resolves_on_the_real_plan(stem):
    exp = _data(EXPECTED / f"{stem}.json")
    p = ROOT / exp["building"]["plan_index"]
    if not p.exists():
        pytest.skip(f"{exp['building']['plan_index']} is local only (data/raw is not in git)")
    index = json.loads(p.read_text())
    llm = exp["llm_only"]
    assert llm["fire"] and llm["people"]
    for item in llm["fire"] + llm["people"]:
        if "room_id" in item:  # one unique room, found from what the caller said
            r = resolve_place(index, **item["resolve"])
            assert r["status"] == "resolved" and r["ids"] == [item["room_id"]], (item["where"], r)
            assert index["rooms"][item["room_id"]]["floor_id"] == item["floor_id"]
        else:  # deliberately kept at floor level
            assert item["floor_id"] in index["floors"]
            if "resolve" in item:
                assert resolve_place(index, **item["resolve"])["status"] != "resolved", item["where"]
    assert exp["floor"]["floor_id"] == llm["fire"][0]["floor_id"]


def _contradictions(exp: dict) -> list:
    return [c for v in _walk(exp) if isinstance(v, dict) for c in (v.get("contradictions") or [])]


def _walk(o):
    yield o
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
