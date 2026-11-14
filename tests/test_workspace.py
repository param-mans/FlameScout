from deepagents.backends import CompositeBackend, StateBackend

from services.agents import mongo_store
from services.agents.workspace import FileWorkspace, StoreWorkspace, incident_workspace


def test_workspace_kind_follows_the_config(storage):
    ws = incident_workspace("inc_a", storage)
    assert isinstance(ws, StoreWorkspace if storage is None else FileWorkspace)


def test_documents_round_trip(storage):
    ws = incident_workspace("inc_a", storage)
    assert ws.list() == [] and ws.read_or_none("brief_v1.json") is None and not ws.exists("brief_v1.json")
    ws.write("brief_v1.json", '{"v": 1}')
    ws.write("layers/costar.json", "{}")
    ws.write("notes/x.md", "note\n")
    first = ws.modified("brief_v1.json")
    ws.write("brief_v1.json", '{"v": 2}')
    assert ws.read("brief_v1.json") == '{"v": 2}' and ws.modified("brief_v1.json") != first
    assert ws.list() == ["brief_v1.json", "layers/costar.json", "notes/x.md"]
    assert ws.list("layers/") == ["layers/costar.json"]
    ws.delete("layers/costar.json")
    ws.delete("layers/costar.json")  # missing is fine
    assert ws.list("layers/") == []
    ws.clear(keep=("notes/x.md",))
    assert ws.list() == ["notes/x.md"]
    assert incident_workspace("inc_b", storage).list() == []  # incidents never see each other's documents


def test_the_agent_sees_the_same_documents(storage):
    """read_file / write_file / ls / glob / grep of the deep agent, mounted at /workspace/ as in the orchestrator."""
    ws = incident_workspace("inc_a", storage)
    ws.write("incident.json", '{"incident_id": "inc_a"}')
    agent_fs = CompositeBackend(default=StateBackend(), routes={"/workspace/": ws.backend()})
    assert agent_fs.read("/workspace/incident.json").file_data["content"] == '{"incident_id": "inc_a"}'
    assert agent_fs.write("/workspace/notes/strategy_reasoning.md", "why\n").error is None
    assert ws.read("notes/strategy_reasoning.md") == "why\n"
    assert [m["path"] for m in agent_fs.glob("**/*.json", "/workspace/").matches] == ["/workspace/incident.json"]
    assert agent_fs.grep("why", "/workspace/").matches[0]["path"] == "/workspace/notes/strategy_reasoning.md"


def test_mongo_documents_keep_parsed_json_for_queries(storage):
    if storage is not None:
        return
    ws = incident_workspace("inc_a", None)
    ws.write("brief_v1.json", '{"version": 1, "size_up": {"building.stories": {"value": 6}}}')
    ws.write("notes/x.md", "text")
    col = mongo_store.default_store().col
    assert col.find_one({"ns": ["incidents", "inc_a"], "doc.version": 1})["key"] == "/brief_v1.json"
    assert col.find_one({"key": "/notes/x.md"})["doc"] is None
