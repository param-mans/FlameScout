-- FlameScout on Tiger Data (TimescaleDB). Apply with: python scripts/init_tiger.py
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS stage_timings (
    ts          timestamptz      NOT NULL DEFAULT now(),
    incident_id text             NOT NULL,
    stage       text             NOT NULL,   -- fusion | locate | dossier | brief | time_to_brief
    ms          double precision NOT NULL,
    meta        jsonb
);
SELECT create_hypertable('stage_timings', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS call_segments (
    ts          timestamptz NOT NULL DEFAULT now(),
    incident_id text        NOT NULL,
    call_id     text        NOT NULL,
    speaker     text        NOT NULL,
    text        text        NOT NULL,
    t_start     double precision,
    t_end       double precision
);
SELECT create_hypertable('call_segments', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS brief_versions (
    ts          timestamptz NOT NULL DEFAULT now(),
    incident_id text        NOT NULL,
    version     int         NOT NULL,
    building_id text,
    n_considerations int,
    n_gaps      int,
    brief       jsonb       NOT NULL
);
SELECT create_hypertable('brief_versions', 'ts', if_not_exists => TRUE);

-- Continuous aggregate for the metrics panel (percentiles are computed from the raw table).
CREATE MATERIALIZED VIEW IF NOT EXISTS stage_timings_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket, stage,
       count(*) AS n, avg(ms) AS avg_ms, max(ms) AS max_ms
FROM stage_timings
GROUP BY bucket, stage
WITH NO DATA;

SELECT add_continuous_aggregate_policy('stage_timings_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);
