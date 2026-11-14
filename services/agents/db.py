"""Postgres on Tiger Data (TimescaleDB): FlameScout's system of record.

  incidents         one row per incident: status, candidates, confirmed building, brief version.
                    Lets an incident survive a server restart (orchestrator._session rehydrates).
  facts             the current value of every fact fusion knows about, one row per (incident, key)
  call_segments     hypertable: every transcript segment fusion ingested
  briefs            every published brief version, with its critique, verdict and timing
  pipeline_events   hypertable: one row per orchestrator call (start / resume / update / prefetch)
  stage_seconds_hourly   continuous aggregate (real-time) over pipeline_events
  time_to_brief     view: seconds of agent work from the first call to brief v1, per incident
  checkpoints ...   LangGraph's PostgresSaver tables: the agent threads themselves

Enabled when TIGER_DATABASE_URL is set. Every write here is best effort: a database problem is
logged and never stops a brief from reaching the crew. Working documents (the agent's
/workspace/) live in MongoDB, see workspace.py.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Iterable, Optional

from flamescout_schemas import Brief, CallSegment, IncidentRecord

log = logging.getLogger(__name__)

SCHEMA = [
    """create table if not exists incidents (
        incident_id   text primary key,
        status        text not null,
        building_id   text,
        candidates    jsonb not null default '[]',
        version       int not null default 1,
        prefetched_at timestamptz,
        created_at    timestamptz not null default now(),
        updated_at    timestamptz not null default now())""",
    """create table if not exists facts (
        incident_id  text not null,
        key          text not null,
        value        jsonb,
        status       text not null,
        confidence   double precision not null,
        evidence_ids text[] not null default '{}',
        updated_at   timestamptz not null,
        primary key (incident_id, key))""",
    """create table if not exists call_segments (
        ts          timestamptz not null default now(),
        incident_id text not null,
        call_id     text not null,
        speaker     text not null,
        text        text not null,
        t_start     double precision,
        t_end       double precision)""",
    "create index if not exists call_segments_incident on call_segments (incident_id, ts desc)",
    """create table if not exists briefs (
        incident_id         text not null,
        version             int not null,
        brief_id            text not null,
        generated_at        timestamptz not null,
        considerations      int not null,
        cross_layer         int not null,
        verdict             text,
        fallback_reason     text,
        verifier_rejections int not null default 0,
        prefetched          boolean not null default false,
        seconds             double precision,
        trace_run_id        text,
        brief               jsonb not null,
        critique            jsonb,
        primary key (incident_id, version))""",
    """create table if not exists pipeline_events (
        ts          timestamptz not null default now(),
        incident_id text not null,
        stage       text not null,
        seconds     double precision not null,
        detail      jsonb not null default '{}')""",
    "create index if not exists pipeline_events_incident on pipeline_events (incident_id, ts desc)",
]

TIMESCALE = [
    "select create_hypertable('call_segments', by_range('ts'), if_not_exists => true)",
    "select create_hypertable('pipeline_events', by_range('ts'), if_not_exists => true)",
    """create materialized view if not exists stage_seconds_hourly
        with (timescaledb.continuous, timescaledb.materialized_only = false) as
        select time_bucket('1 hour', ts) as bucket, stage,
               count(*) as runs, avg(seconds) as avg_seconds, max(seconds) as max_seconds
        from pipeline_events group by bucket, stage with no data""",
]

VIEWS = [
    """create or replace view time_to_brief as
        select ts, incident_id, (detail->>'time_to_brief_s')::double precision as seconds,
               coalesce((detail->>'prefetched')::boolean, false) as prefetched,
               coalesce((detail->>'fallback')::boolean, false) as fallback
        from pipeline_events where stage = 'resume' and detail ? 'time_to_brief_s'""",
]

_LOCK = threading.RLock()
_POOL = None
_READY = False


def enabled() -> bool:
    return bool(os.environ.get("TIGER_DATABASE_URL"))


def pool():
    """The process-wide connection pool (autocommit, dict rows: what PostgresSaver needs)."""
    global _POOL
    with _LOCK:
        if _POOL is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            _POOL = ConnectionPool(os.environ["TIGER_DATABASE_URL"], min_size=1, max_size=8, open=True,
                                   kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                                           "connect_timeout": 10})
            atexit.register(reset)
        return _POOL


def init() -> None:
    """Create the tables, hypertables, continuous aggregate and checkpointer tables. Idempotent."""
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        with pool().connection() as conn:
            for sql in SCHEMA:
                conn.execute(sql)
            for sql in TIMESCALE:  # plain Postgres still works without them
                try:
                    conn.execute(sql)
                except Exception as e:
                    log.warning("TimescaleDB step skipped (%s): %s", type(e).__name__, str(e).splitlines()[0])
            for sql in VIEWS:
                conn.execute(sql)
        checkpointer().setup()
        _READY = True


_SAVER = None


def checkpointer():
    """LangGraph PostgresSaver on the same pool: agent threads survive a restart."""
    global _SAVER
    if _SAVER is None:
        from langgraph.checkpoint.postgres import PostgresSaver

        _SAVER = PostgresSaver(pool())
    return _SAVER


def _execute(sql: str, params: Any = None, many: bool = False) -> None:
    """Best-effort write. Never raises."""
    if not enabled():
        return
    try:
        init()
        with pool().connection() as conn:
            if many:
                with conn.cursor() as cur:
                    cur.executemany(sql, params)
            else:
                conn.execute(sql, params)
    except Exception:
        log.exception("Tiger write failed; continuing without it")


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------- writes ----------

def save_incident_state(incident_id: str, status: str, candidates: list[dict], building_id: Optional[str],
                        version: int, prefetched_at: Optional[datetime]) -> None:
    _execute("""insert into incidents (incident_id, status, building_id, candidates, version, prefetched_at)
                values (%s, %s, %s, %s::jsonb, %s, %s)
                on conflict (incident_id) do update set status = excluded.status,
                    building_id = excluded.building_id, candidates = excluded.candidates,
                    version = excluded.version, prefetched_at = excluded.prefetched_at, updated_at = now()""",
             (incident_id, status, building_id, _json(candidates), version, prefetched_at))


def save_facts(record: IncidentRecord) -> None:
    rows = [(record.incident_id, f.key, _json(f.value), f.status, f.confidence, f.evidence_ids, f.updated_at)
            for f in record.facts.values()]
    if rows:
        _execute("""insert into facts (incident_id, key, value, status, confidence, evidence_ids, updated_at)
                    values (%s, %s, %s::jsonb, %s, %s, %s, %s)
                    on conflict (incident_id, key) do update set value = excluded.value,
                        status = excluded.status, confidence = excluded.confidence,
                        evidence_ids = excluded.evidence_ids, updated_at = excluded.updated_at""",
                 rows, many=True)


def save_segments(incident_id: str, segments: Iterable[CallSegment]) -> None:
    rows = [(incident_id, s.call_id, s.speaker, s.text, s.t_start, s.t_end) for s in segments]
    if rows:
        _execute("""insert into call_segments (incident_id, call_id, speaker, text, t_start, t_end)
                    values (%s, %s, %s, %s, %s, %s)""", rows, many=True)


def save_brief(brief: Brief, critique: Optional[dict], fallback_reason: Optional[str], seconds: float,
               trace_run_id: Optional[str]) -> None:
    _execute("""insert into briefs (incident_id, version, brief_id, generated_at, considerations, cross_layer,
                    verdict, fallback_reason, verifier_rejections, prefetched, seconds, trace_run_id, brief, critique)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                on conflict (incident_id, version) do update set brief_id = excluded.brief_id,
                    generated_at = excluded.generated_at, considerations = excluded.considerations,
                    cross_layer = excluded.cross_layer, verdict = excluded.verdict,
                    fallback_reason = excluded.fallback_reason,
                    verifier_rejections = excluded.verifier_rejections, prefetched = excluded.prefetched,
                    seconds = excluded.seconds, trace_run_id = excluded.trace_run_id,
                    brief = excluded.brief, critique = excluded.critique""",
             (brief.incident_id, brief.version, brief.brief_id, brief.generated_at, len(brief.considerations),
              sum(c.cross_layer for c in brief.considerations), (critique or {}).get("verdict"),
              fallback_reason, brief.verifier_rejections, bool(brief.freshness.get("prefetched")), seconds,
              trace_run_id, brief.model_dump_json(), _json(critique) if critique else None))


def forget_briefs(incident_id: str) -> None:
    """start_incident starts an incident over: its earlier brief versions no longer apply."""
    _execute("delete from briefs where incident_id = %s", (incident_id,))


def reset_incident(incident_id: str) -> None:
    """Drop everything recorded for an incident id except its timings (scripts re-run the same id)."""
    for table in ("incidents", "facts", "call_segments", "briefs"):
        _execute(f"delete from {table} where incident_id = %s", (incident_id,))


def log_event(incident_id: str, stage: str, seconds: float, **detail: Any) -> None:
    _execute("insert into pipeline_events (incident_id, stage, seconds, detail) values (%s, %s, %s, %s::jsonb)",
             (incident_id, stage, seconds, _json(detail)))


# ---------- reads ----------

def load_incident_state(incident_id: str) -> Optional[dict]:
    """The incidents row, or None (also when the database is off or unreachable)."""
    if not enabled():
        return None
    try:
        init()
        with pool().connection() as conn:
            return conn.execute("select * from incidents where incident_id = %s", (incident_id,)).fetchone()
    except Exception:
        log.exception("Tiger read failed")
        return None


def time_to_brief_stats() -> dict:
    """P50 / P95 seconds of agent work to brief v1, split by whether the building was prefetched.
    Facts-only fallbacks (the agent failed) are left out: they are fast because nothing ran."""
    if not enabled():
        return {}
    init()
    with pool().connection() as conn:
        rows = conn.execute("""select prefetched, count(*) as runs,
                    percentile_cont(0.5) within group (order by seconds) as p50,
                    percentile_cont(0.95) within group (order by seconds) as p95
                from time_to_brief where not fallback group by prefetched""").fetchall()
    return {("warm" if r["prefetched"] else "cold"): {"runs": r["runs"], "p50": r["p50"], "p95": r["p95"]}
            for r in rows}


def reset() -> None:
    """Close the pool and forget state (tests)."""
    global _POOL, _READY, _SAVER
    if _POOL is not None:
        _POOL.close(timeout=2)
    _POOL, _READY, _SAVER = None, False, None
