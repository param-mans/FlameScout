import json
import os
import uuid
from pathlib import Path

import pytest

from flamescout_schemas import Brief, CallSegment
from services.agents import db
from services.agents.fusion import FusionEngine

CALLS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "calls"


def test_everything_is_a_no_op_without_tiger(monkeypatch):
    def no_pool():
        raise AssertionError("touched the database although TIGER_DATABASE_URL is unset")

    monkeypatch.setattr(db, "pool", no_pool)
    assert not db.enabled()
    db.save_incident_state("inc_x", "awaiting_confirmation", [], None, 1, None)
    db.log_event("inc_x", "start", 1.0)
    assert db.load_incident_state("inc_x") is None and db.time_to_brief_stats() == {}


def test_a_broken_database_never_raises(monkeypatch):
    def broken():
        raise ConnectionError("unreachable")

    monkeypatch.setenv("TIGER_DATABASE_URL", "postgresql://nowhere")
    monkeypatch.setattr(db, "pool", broken)
    db.log_event("inc_x", "start", 1.0)
    db.save_incident_state("inc_x", "briefed", [], "b", 1, None)
    assert db.load_incident_state("inc_x") is None


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TIGER_DATABASE_URL"), reason="TIGER_DATABASE_URL not set")
def test_tiger_round_trip():
    incident_id = f"test_{uuid.uuid4().hex[:8]}"
    segments = [CallSegment(**s) for s in json.loads((CALLS / "s2.json").read_text())["segments"]]
    eng = FusionEngine(incident_id)
    for s in segments:
        eng.ingest(s)
    try:
        db.save_segments(incident_id, segments)
        db.save_facts(eng.record)
        db.save_incident_state(incident_id, "briefed", [{"building_id": "b", "name": "n", "address": "a",
                                                         "score": 1.0}], "b", 2, None)
        brief = Brief(brief_id=f"{incident_id}_v1", incident_id=incident_id, version=1, location_confirmed=True)
        db.save_brief(brief, {"verdict": "pass", "issues": []}, None, 12.5, None)
        db.log_event(incident_id, "resume", 12.5, time_to_brief_s=20.0, prefetched=False)

        row = db.load_incident_state(incident_id)
        assert (row["status"], row["building_id"], row["version"]) == ("briefed", "b", 2)
        assert row["candidates"][0]["building_id"] == "b"
        with db.pool().connection() as conn:
            q = lambda sql: conn.execute(sql, (incident_id,)).fetchone()  # noqa: E731
            assert q("select count(*) as n from call_segments where incident_id = %s")["n"] == len(segments)
            assert q("select count(*) as n from facts where incident_id = %s")["n"] == len(eng.record.facts)
            assert q("select verdict from briefs where incident_id = %s")["verdict"] == "pass"
            assert q("select seconds from time_to_brief where incident_id = %s")["seconds"] == 20.0
            assert conn.execute("select 1 from timescaledb_information.hypertables "
                                "where hypertable_name = 'pipeline_events'").fetchone()
        assert db.time_to_brief_stats()["cold"]["runs"] >= 1
    finally:
        with db.pool().connection() as conn:
            for table in ("incidents", "facts", "call_segments", "briefs", "pipeline_events"):
                conn.execute(f"delete from {table} where incident_id = %s", (incident_id,))
