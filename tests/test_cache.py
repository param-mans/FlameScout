import pytest
from datetime import timedelta

from flamescout_schemas import now
from services.agents import cache
from services.agents.dossier import LAYERS

BUILDING = "fake_wilshire_tower"


@pytest.fixture(autouse=True)
def _both_storages(storage):
    """Every cache test runs on local files and on MongoDB."""


def test_no_cache_means_nothing_to_reuse():
    assert cache.load_fresh(BUILDING) is None


def test_fresh_cache_round_trips(make_cache):
    at = make_cache(BUILDING)
    hit = cache.load_fresh(BUILDING)
    assert hit.prefetched_at == at and set(hit.layers) == set(LAYERS)
    assert hit.layers["vexcel"].markers and hit.notes == {"costar": "cached costar note\n"}


def test_cache_older_than_24_hours_is_ignored(make_cache):
    make_cache(BUILDING, at=now() - timedelta(hours=23, minutes=59))
    assert cache.load_fresh(BUILDING) is not None
    make_cache(BUILDING, at=now() - timedelta(hours=24, minutes=1))
    assert cache.load_fresh(BUILDING) is None
    make_cache(BUILDING, at=now() + timedelta(hours=1))  # a clock gone wrong is not "fresh"
    assert cache.load_fresh(BUILDING) is None


def test_incomplete_or_foreign_cache_is_ignored(make_cache):
    make_cache(BUILDING)
    ws = cache.cache_workspace(BUILDING)
    ws.delete("layers/vexcel.json")
    assert cache.load_fresh(BUILDING) is None

    make_cache(BUILDING)
    ws.write("cache.json", '{"building_id": "other", "prefetched_at": "%s"}' % now().isoformat())
    assert cache.load_fresh(BUILDING) is None
    ws.write("cache.json", "not json")
    assert cache.load_fresh(BUILDING) is None
