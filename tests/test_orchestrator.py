import json
import os
from pathlib import Path

import pytest

from flamescout_schemas import Brief, BuildingDossier, CallSegment, Progress
from services.agents import orchestrator
from services.agents.fusion import FusionEngine
from services.agents.orchestrator import MAX_SUBMISSIONS, make_tools, run_incident

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"
GOOD = {"category": "access", "title": "Consider Stair B from the rear alley",
        "rationale": "A caller reported someone shouting from the back stairs.",
        "evidence_ids": ["ev_scan_fp_4"]}


def make_workspace(root: Path, building_id: str | None = "fake_wilshire_tower") -> Path:
    data = json.loads((CALLS / "s2.json").read_text())
    rec = FusionEngine("inc_s2").ingest_all(CallSegment(**s) for s in data["segments"])
    rec.confirmed_building_id = building_id
    ws = root / "inc_s2"
    ws.mkdir()
    (ws / "incident.json").write_text(rec.model_dump_json(indent=2))
    return ws


@pytest.fixture
def ws(tmp_path):
    return make_workspace(tmp_path)


def test_load_dossier_tool_writes_the_confirmed_buildings_dossier(ws):
    out = make_tools(ws)["load_dossier"].invoke({})
    assert "ev_costar_1" in out
    assert BuildingDossier.model_validate_json((ws / "dossier.json").read_text()).building_id == "fake_wilshire_tower"


def test_submit_considerations_writes_a_valid_brief(ws):
    tools = make_tools(ws)
    assert "does not exist yet" in tools["submit_considerations"].invoke({"considerations": [GOOD]})
    tools["load_dossier"].invoke({})
    assert tools["submit_considerations"].invoke({"considerations": [GOOD]}).startswith("Accepted")
    brief = Brief.model_validate_json((ws / "brief_v1.json").read_text())
    assert [c.title for c in brief.considerations] == [GOOD["title"]]
    assert all(e in brief.evidence for c in brief.considerations for e in c.evidence_ids)


def test_rejected_considerations_write_nothing(ws):
    tools = make_tools(ws)
    tools["load_dossier"].invoke({})
    bad = [{**GOOD, "title": "Send the truck to Stair B"}, {**GOOD, "evidence_ids": ["ev_made_up"]}]
    out = tools["submit_considerations"].invoke({"considerations": bad})
    assert out.startswith("Rejected") and "#1" in out and "ev_made_up" in out
    assert not (ws / "brief_v1.json").exists()


def test_regeneration_budget_and_stale_critique(ws):
    tools = make_tools(ws)
    tools["load_dossier"].invoke({})
    assert "does not exist yet" in tools["submit_critique"].invoke({"verdict": "pass", "issues": []})
    tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert "needs at least one issue" in tools["submit_critique"].invoke({"verdict": "revise", "issues": []})
    tools["submit_critique"].invoke({"verdict": "revise", "issues": ["c1: not supported"]})
    assert json.loads((ws / "critique_v1.json").read_text())["verdict"] == "revise"
    for _ in range(MAX_SUBMISSIONS - 1):
        tools["submit_considerations"].invoke({"considerations": [GOOD]})
    assert not (ws / "critique_v1.json").exists()  # it was about an older brief
    assert "budget used up" in tools["submit_considerations"].invoke({"considerations": [GOOD]})


def test_run_incident_needs_a_confirmed_building(tmp_path):
    make_workspace(tmp_path, building_id=None)
    with pytest.raises(ValueError, match="no confirmed building"):
        run_incident("inc_s2", workspace_root=tmp_path)


def test_failed_run_falls_back_to_a_facts_only_brief(tmp_path, monkeypatch):
    ws = make_workspace(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(orchestrator, "build_orchestrator", boom)
    result = run_incident("inc_s2", workspace_root=tmp_path)
    brief = Brief.model_validate_json((ws / "brief_v1.json").read_text())
    assert result.fallback_reason and brief.considerations == []
    assert brief.gaps[0].startswith("Considerations withheld")
    assert brief.size_up["building.stories"].value == 6
    assert Progress.model_validate_json((ws / "todos.json").read_text()).incident_id == "inc_s2"


@pytest.mark.live
@pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_FAST_MODEL")),
                    reason="GEMINI_API_KEY / GEMINI_FAST_MODEL not set")
def test_orchestrator_end_to_end_on_s2(tmp_path):
    ws = make_workspace(tmp_path)
    seen: list[Progress] = []
    result = run_incident("inc_s2", workspace_root=tmp_path, on_todos=seen.append)
    orchestrator.flush_traces()
    brief = Brief.model_validate_json((ws / "brief_v1.json").read_text())
    assert len(brief.considerations) <= 5
    assert all(c.title.startswith("Consider ") and c.evidence_ids for c in brief.considerations)
    assert all(e in brief.evidence for c in brief.considerations for e in c.evidence_ids)
    assert result.fallback_reason or brief.considerations
    assert any(p.todos for p in seen) and all(t.status == "completed" for t in result.todos)
    assert (ws / "notes" / "dossier_summary.md").exists()
    assert (ws / "notes" / "strategy_reasoning.md").exists()
