import json
import logging

import pytest

from services.agents.providers import (FixtureCoStarProvider, FixtureMatterportProvider,
                                       FixtureVexcelProvider, distance_m, get_providers)

BUILDING = "fake_wilshire_tower"


def test_costar_fixture_search_and_get():
    costar = FixtureCoStarProvider()
    assert [p["property_id"] for p in costar.search_property("1200 Wilshire Blvd")] == [BUILDING]
    assert costar.search_property("77 Elm Street") == []
    p = costar.get_property(BUILDING)
    assert p["address"] == "1200 Wilshire Boulevard" and "building.stories" in p["facts"]
    with pytest.raises(FileNotFoundError, match="No CoStar fixture"):
        costar.get_property("no_such_building")
    with pytest.raises(ValueError, match="Bad building id"):
        costar.get_property("../calls")


def test_matterport_fixture_model_floorplans_and_frames():
    matterport = FixtureMatterportProvider()
    model = matterport.find_model({"property_id": BUILDING})
    assert model["captured_at"].startswith("2026-06-12")
    assert [f["floor"] for f in matterport.get_floorplans(model["model_id"])] == [1, 4]
    assert len(matterport.get_frames(model["model_id"], None)) == 1
    assert matterport.get_frames(model["model_id"], 4) == []
    assert matterport.find_model({"property_id": "no_such_building"}) is None


def test_matterport_fixture_without_a_scan(tmp_path):
    (tmp_path / "b1").mkdir()
    (tmp_path / "b1" / "matterport.json").write_text(json.dumps({"model": None}))
    assert FixtureMatterportProvider(tmp_path).find_model({"property_id": "b1"}) is None


def test_vexcel_fixture_respects_the_radius():
    vexcel = FixtureVexcelProvider()
    assert len(vexcel.get_aerial(34.05, -118.26, 150)) == 2
    assert vexcel.get_aerial(34.06, -118.26, 150) == []  # about 1.1 km north
    assert 1000 < distance_m(34.05, -118.26, 34.06, -118.26) < 1200


def test_live_mode_logs_not_configured_and_serves_stored_data_and_fixtures(monkeypatch, caplog):
    from services.agents import providers as module

    monkeypatch.setattr(module, "_LIVE_WARNED", False)
    monkeypatch.setenv("DATA_MODE", "live")
    with caplog.at_level(logging.WARNING):
        providers = get_providers()
        get_providers()
        assert providers.costar.get_property(BUILDING)["name"] == "Wilshire Tower"  # no data/raw: fixtures
        assert providers.matterport.find_model({"property_id": BUILDING})
        assert providers.vexcel.get_aerial(34.05, -118.26, 150)
    assert [r.getMessage() for r in caplog.records] == [
        "Live API calls are not configured; using stored API responses (data/raw) and fixtures"]
    monkeypatch.setenv("DATA_MODE", "fixture")
    assert isinstance(get_providers().costar, module.LiveCoStarProvider)  # same chain in both modes
