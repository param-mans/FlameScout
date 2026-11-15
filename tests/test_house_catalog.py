"""House catalog (data/raw/catalog.csv) as a locator source, and on-demand plans from the house data service.

Offline: a synthetic catalog in a tmp data/raw and a fake house data service on 127.0.0.1:81xx. Nothing is written to the
real data/raw. The last test reads the real catalog (skipped without it)."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from flamescout_schemas import Fact, IncidentRecord
from services.agents import brief, dossier, house_catalog, locator, overlay, providers
from services.agents.tools import house_data_client
from services.api.config import Settings
from services.api.main import create_app
from services.api.service import auto_confirm_pick

from .conftest import FIXTURES

HEAD = ("listing_id,matterport_model_id,address,city,state,zip,floors,bedrooms,bathrooms,sqft,year_built,room_types,"
        "exterior_doors,stairs,quality")
ROWS = [
    "900000001,AbCdEf00001,9660 Fairdale Lane,Houston,TX,77063,2,3,3,3480,1999,bathroom|kitchen,5,1,ok",
    "900000002,AbCdEf00002,12 Elm Street,Houston,TX,77001,1,2,1,900,1950,kitchen,1,0,ok",
    "900000003,AbCdEf00003,40 Elm Street,Houston,TX,77001,1,3,2,1200,1960,kitchen,2,0,ok",
    "900000004,AbCdEf00004,7 Elm Street,Los Angeles,CA,90001,1,3,2,1200,1960,kitchen,2,0,ok",
    "900000005,AbCdEf00005,318 W Oak Court,Houston,TX,77002,1,3,2,1200,1960,kitchen,2,0,ok",
]
HOUSE = "900000001"
PLAN = {"modelId": "AbCdEf00001", "source": "api-edges", "units": "meters", "floors": [
    {"id": "f1", "label": "Floor 1", "rooms": [
        {"id": "k1", "label": "", "type": "Kitchen", "areaM2": 12.0},
        {"id": "b1", "label": "", "type": "Bathroom", "areaM2": 5.0},
        {"id": "v1", "label": "", "type": "Void", "areaM2": 1.0}],
     "openings": [{"id": "d1", "type": "doorway", "exterior": True}, {"id": "d2", "type": "doorway", "exterior": False}]}],
    "stairs": []}
INDEX = {"version": 2, "floors": ["f1"], "openings": {}, "stairs": [], "rooms": {
    "k1": {"floor_id": "f1", "label": "KITCHEN", "type": "kitchen", "area_m2": 12.0, "size_rank": 1, "size_of": 1,
           "next_to": ["b1"], "over": [], "under": [], "at_stairs": False},
    "b1": {"floor_id": "f1", "label": "BATHROOM", "type": "bathroom", "area_m2": 5.0, "size_rank": 1, "size_of": 1,
           "next_to": ["k1"], "over": [], "under": [], "at_stairs": False}}}


@pytest.fixture
def raw(tmp_path, monkeypatch):
    root = tmp_path / "raw_catalog"
    root.mkdir()
    (root / "catalog.csv").write_text("\n".join([HEAD, *ROWS]) + "\n")
    monkeypatch.setattr(providers, "RAW_ROOT", root)
    monkeypatch.setattr(overlay, "PLAN_INDEX_ROOT", root)
    return root


class FakeService:
    """The house data service's plan route: GET /api/house-viewer/plan/<id>. mode: ok | fail (503)."""

    def __init__(self):
        self.mode, self.hits = "ok", []
        svc = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                svc.hits.append(self.path)
                listing = self.path.rsplit("/", 1)[-1]
                if svc.mode == "ok" and listing == HOUSE:
                    body, code = {"id": HOUSE, "model_id": "AbCdEf00001", "plan_source": "live", "plan": PLAN,
                                  "plan_index": INDEX}, 200
                else:
                    body, code = {"error": "Matterport API token is not set"}, 503
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        for port in range(8100, 8110):
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        else:
            pytest.skip("no free port in 8100-8109")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def service(monkeypatch):
    svc = FakeService()
    monkeypatch.setenv("HOUSE_DATA_URL", svc.url)
    yield svc
    svc.close()


# ---------- on-demand plans ----------

def test_a_catalog_house_without_folder_is_fetched_once_and_cached(raw, service):
    index = overlay.load_plan_index(HOUSE)
    assert set(index["rooms"]) == {"k1", "b1"} and len(service.hits) == 1
    assert service.hits[0] == f"/api/house-viewer/plan/{HOUSE}"
    plan_path, index_path = house_data_client.plan_paths(raw, HOUSE)
    assert json.loads(plan_path.read_text())["modelId"] == "AbCdEf00001"
    assert json.loads(index_path.read_text()) == INDEX
    # next time: read from data/raw, the service is not called
    assert overlay.load_plan_index(HOUSE)["rooms"].keys() == {"k1", "b1"}
    layer = dossier.pull_matterport(HOUSE)
    assert len(service.hits) == 1
    assert layer.scan_available and layer.matterport_model_id == "AbCdEf00001"
    assert layer.facts["building.floors_scanned"].value == 1
    assert layer.facts["building.rooms"].value == 2          # Void and < 2 m2 left out
    assert [m.id for m in layer.markers] == ["m_exit_d1"]     # the service's exterior flag, lowest floor


def test_a_failing_service_means_no_scan_and_nothing_crashes(raw, service):
    service.mode = "fail"
    assert overlay.load_plan_index(HOUSE) == overlay.EMPTY_INDEX
    layer = dossier.pull_matterport(HOUSE)
    assert not layer.scan_available
    assert not (raw / HOUSE).exists()                         # nothing cached
    assert len(service.hits) == 1                             # the failure is remembered for a while
    # the listing data still comes from the catalog row; no coordinates, so no aerial imagery
    costar = dossier.pull_costar(HOUSE)
    assert costar.facts["building.bedrooms"].value == 3 and costar.address.startswith("9660 Fairdale Lane, Houston")
    assert dossier.pull_vexcel(HOUSE).markers == []
    d = dossier.load_dossier(HOUSE)
    assert not d.scan_available
    assert "No interior scan on file" in brief._gaps(IncidentRecord(incident_id="inc_x"), d)


def test_no_service_at_all_means_no_scan(raw, monkeypatch):
    with socket.socket() as s:   # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("HOUSE_DATA_URL", f"http://127.0.0.1:{port}")
    assert not house_data_client.ensure_plan(HOUSE, raw)
    assert overlay.load_plan_index(HOUSE) == overlay.EMPTY_INDEX


def test_ids_outside_the_catalog_are_never_fetched(raw, service):
    assert overlay.load_plan_index("123456789") == overlay.EMPTY_INDEX
    assert providers.raw_plan("123456789") is None
    assert overlay.load_plan_index("fake_wilshire_tower") == overlay.EMPTY_INDEX
    assert service.hits == []


# ---------- address locator ----------

def incident(address: str) -> IncidentRecord:
    rec = IncidentRecord(incident_id="inc_cat")
    rec.facts["location.address"] = Fact(key="location.address", value=address, status="reported", confidence=0.6,
                                         evidence_ids=["ev1"])
    return rec


def ranked(address: str):
    return locator.find_candidates(incident(address), providers.get_providers(FIXTURES))


@pytest.mark.parametrize("said, score", [
    ("9660 Fairdale", 0.85),
    ("9660 Fairdale Ln", 0.9),
    ("nine six six oh Fairdale lane", 0.9),
    ("ninety six sixty Fairdale Lane", 0.9),
    ("9660 FAIRDALE LANE", 0.9),
    ("9660 Fairdale Lane, Houston", 1.0),
    ("9660 fairdale lane houston tx 77063", 1.0),
])
def test_address_variants_resolve_to_one_house_and_auto_confirm(raw, said, score):
    cands = ranked(said)
    assert [(c.building_id, c.score) for c in cands] == [(HOUSE, score)]
    assert cands[0].scan_available is True                     # catalog house: no fetch at locate time
    assert auto_confirm_pick(cands) == HOUSE


def test_a_number_that_does_not_exist_gives_no_candidate(raw):
    assert ranked("9661 Fairdale Lane") == []
    assert ranked("9660 Fairdale Lane, Los Angeles") == []     # a city that was said must match


def test_a_street_without_number_needs_the_dispatcher(raw):
    cands = ranked("Elm Street")
    assert {c.building_id for c in cands} == {"900000002", "900000003", "900000004"}
    assert auto_confirm_pick(cands) is None
    houston = ranked("Elm Street, Houston")
    assert {c.building_id for c in houston} == {"900000002", "900000003"} and auto_confirm_pick(houston) is None
    assert [c.building_id for c in ranked("12 Elm St")] == ["900000002"]


def test_directions_and_suffixes_are_normalized(raw):
    assert [c.building_id for c in ranked("318 West Oak Ct")] == ["900000005"]
    assert ranked("318 East Oak Court") == []
    assert house_catalog.normalize("Nine-Six-Six-Oh N. Fairdale Boulevard") == ["9660", "n", "fairdale", "blvd"]
    assert house_catalog.words_to_digits(["oh", "no"]) == ["oh", "no"]          # "oh" alone is not a number
    assert house_catalog.words_to_digits(["eighteen", "one", "oh", "seven"]) == ["18107"]


def test_fixture_buildings_still_resolve_without_a_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "RAW_ROOT", tmp_path / "empty")
    cands = ranked("1200 Wilshire Boulevard")
    assert cands[0].building_id == "fake_wilshire_tower" and auto_confirm_pick(cands) == "fake_wilshire_tower"


def test_address_not_in_catalog_leaves_the_incident_without_a_house(raw, monkeypatch):
    monkeypatch.setenv("FAST_PIPELINE", "0")
    app = create_app(Settings(data_mode="fixture", workspace=raw.parent / "ws_nc", fixtures=FIXTURES, tiger_url=None,
                              cors_origins=["*"], pipeline="orchestrator"))
    with TestClient(app) as client:
        client.post("/api/incidents", json={"incident_id": "inc_nc"})
        r = client.post("/api/incidents/inc_nc/segments", json={
            "call_id": "c1", "speaker": "caller", "text": "There's a fire at 1234 Nowhere Road!", "t_start": 0.0,
            "t_end": 3.0})
        assert r.status_code == 200
        client.app.state.svc.wait("inc_nc")
        inc = client.get("/api/incidents/inc_nc").json()
        assert inc["confirmed_building_id"] is None
        assert client.get("/api/incidents/inc_nc/candidates").json() == []
        questions = [s["question"] for s in client.get("/api/incidents/inc_nc/copilot").json()]
        assert questions[0].startswith("Address not in catalog: 1234 Nowhere Road")
        events = [e for e in client.app.state.bus.history("inc_nc") if e.type == "copilot.suggestion"]
        assert any(s["question"].startswith("Address not in catalog") for s in events[-1].payload["suggestions"])


# ---------- the real catalog (local only) ----------

@pytest.mark.real_data
@pytest.mark.parametrize("said, want, auto", [
    ("9660 Fairdale", "209217448", True),
    ("9660 Fairdale Ln", "209217448", True),
    ("nine six six oh Fairdale lane", "209217448", True),
    ("9660 FAIRDALE LANE", "209217448", True),
    ("9660 Fairdale Lane, Houston", "209217448", True),
    ("9661 Fairdale Lane", None, False),
    ("High Point Lane", "several", False),
])
def test_real_catalog_variants(said, want, auto):
    if not (providers.RAW_ROOT / "catalog.csv").exists():
        pytest.skip("data/raw/catalog.csv is local (scripts/build_house_catalog.mjs)")
    cands = locator.find_candidates(incident(said), providers.get_providers(FIXTURES))
    if want is None:
        assert cands == []
    elif want == "several":
        assert len(cands) >= 2 and auto_confirm_pick(cands) is None
    else:
        assert cands[0].building_id == want and (auto_confirm_pick(cands) == want) == auto
