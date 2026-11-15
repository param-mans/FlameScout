"""overlay.updated over the API, end to end and offline (S5, fake_cedar_house).

No model is configured, so the orchestrator writes its facts-only brief and, for the same version, the empty
fallback overlay (orchestrator._ensure_overlay). The service relays that file as overlay.updated right after
brief.updated. Checks: event order, equal versions, same trace id, the overlay validates against the house's plan
index with packages/schemas/overlay/validate_overlay.py, and routes is empty (the planner runs in the viewer)."""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from services.api.config import Settings
from services.api.main import create_app

from .conftest import FIXTURES, ROOT, load_script

HOUSE = "fake_cedar_house"
VALIDATOR = ROOT / "packages" / "schemas" / "overlay" / "validate_overlay.py"


def _validator():
    if not VALIDATOR.exists():
        pytest.skip(f"{VALIDATOR.relative_to(ROOT)} is missing")
    spec = importlib.util.spec_from_file_location("validate_overlay", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_CONFIRM_LOCATION", "0")   # the test drives the dispatcher's confirmation
    if not (FIXTURES / "calls" / "s5.json").exists() or not (FIXTURES / HOUSE).exists():
        pytest.skip("S5 fixtures (data/fixtures/calls/s5.json, data/fixtures/fake_cedar_house) are missing")
    app = create_app(Settings(data_mode="fixture", workspace=tmp_path / "ws", fixtures=FIXTURES,
                              tiger_url=None, cors_origins=["*"], pipeline="orchestrator"))
    with TestClient(app) as c:
        yield c


def test_s5_overlay_updated_follows_brief_updated_and_validates(client, cedar_plan_index):
    svc, bus = client.app.state.svc, client.app.state.bus
    client.post("/api/incidents", json={"incident_id": "inc_s5"})
    for s in load_script("S5"):
        if s.call_id == "c1":
            client.post("/api/incidents/inc_s5/segments", json=s.model_dump())
    svc.wait("inc_s5")
    r = client.post("/api/incidents/inc_s5/confirm-location", json={"building_id": HOUSE})
    assert r.status_code == 202
    svc.wait("inc_s5")

    hist = bus.history("inc_s5")
    briefs = [n for n, e in enumerate(hist) if e.type == "brief.updated"]
    assert briefs, "no brief.updated was emitted"
    for i in briefs:
        brief, ev = hist[i], hist[i + 1]
        assert ev.type == "overlay.updated", f"after brief.updated came {ev.type}"
        assert ev.incident_id == brief.incident_id == "inc_s5" and ev.trace_id == brief.trace_id
        ov = ev.payload["overlay"]
        assert ev.payload["version"] == brief.payload["version"] == ov["version"]
        assert ov["incident_id"] == "inc_s5"
        assert ov["routes"] == []
        assert _validator().validate(ov, cedar_plan_index) == []
