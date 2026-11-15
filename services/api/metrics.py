"""Pipeline timings + persistence (PRD FR-13).

Always appends to workspace/_metrics/timings.jsonl (works offline, used by evals).
If TIGER_DATABASE_URL is set, also writes to Tiger Data on a background thread so
DB latency never touches the < 3 s fusion path. Tiger errors are logged and the
writer backs off; the demo keeps working from the JSONL file.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from flamescout_schemas import Brief, CallSegment

from .store import Workspace

log = logging.getLogger("flamescout.metrics")
BACKOFF_S = 60.0


class Metrics:
    def __init__(self, ws: Workspace, tiger_url: Optional[str]):
        self.ws = ws
        self.tiger_url = tiger_url
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tiger") if tiger_url else None
        self._down_until = 0.0

    @property
    def tiger_enabled(self) -> bool:
        return self._pool is not None

    # ---------- writes ----------
    def record(self, incident_id: str, stage: str, ms: float, **meta: Any) -> dict:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "incident_id": incident_id,
               "stage": stage, "ms": round(ms, 1), "meta": meta}
        self.ws.append_jsonl(self.ws.metrics_file(), row)
        self._tiger("INSERT INTO stage_timings (incident_id, stage, ms, meta) VALUES (%s, %s, %s, %s)",
                    (incident_id, stage, ms, json.dumps(meta, default=str)))
        return row

    def log_segment(self, incident_id: str, seg: CallSegment) -> None:
        self._tiger("INSERT INTO call_segments (incident_id, call_id, speaker, text, t_start, t_end) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (incident_id, seg.call_id, seg.speaker, seg.text, seg.t_start, seg.t_end))

    def log_brief(self, brief: Brief) -> None:
        self._tiger("INSERT INTO brief_versions (incident_id, version, building_id, n_considerations, n_gaps, brief) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (brief.incident_id, brief.version, brief.building_id, len(brief.considerations),
                     len(brief.gaps), brief.model_dump_json()))

    def _tiger(self, sql: str, params: tuple) -> None:
        if self._pool is None or time.monotonic() < self._down_until:
            return
        self._pool.submit(self._exec, sql, params)

    def _exec(self, sql: str, params: tuple) -> None:
        import psycopg
        try:
            with psycopg.connect(self.tiger_url, autocommit=True, connect_timeout=3) as conn:  # type: ignore[arg-type]
                conn.execute(sql, params)
        except Exception as e:  # noqa: BLE001
            self._down_until = time.monotonic() + BACKOFF_S
            log.warning("tiger write failed, backing off %ss: %s", BACKOFF_S, e)

    # ---------- reads ----------
    def time_to_brief(self, limit: int = 100) -> dict:
        rows = self._tiger_ttb(limit) if self.tiger_enabled else None
        source = "tiger" if rows is not None else "workspace"
        if rows is None:
            rows = [{"ts": r["ts"], "incident_id": r["incident_id"], "ms": r["ms"],
                     "cached_dossier": r.get("meta", {}).get("cached_dossier"), "fallback": False}
                    for r in self.ws.read_jsonl(self.ws.metrics_file()) if r["stage"] == "time_to_brief"][-limit:]
        # Percentiles only over runs where the agent produced the brief: a facts-only fallback is not a brief time.
        ms = sorted(r["ms"] for r in rows if not r.get("fallback"))
        return {"source": source, "n": len(ms), "n_fallback": len(rows) - len(ms), "runs": rows,
                "p50_ms": round(statistics.median(ms), 1) if ms else None,
                "p95_ms": round(ms[min(len(ms) - 1, int(0.95 * len(ms)))], 1) if ms else None}

    def stage_summary(self) -> dict[str, dict]:
        by: dict[str, list[float]] = {}
        for r in self.ws.read_jsonl(self.ws.metrics_file()):
            by.setdefault(r["stage"], []).append(r["ms"])
        return {k: {"n": len(v), "p50_ms": round(statistics.median(v), 1), "max_ms": round(max(v), 1)}
                for k, v in by.items()}

    def _tiger_ttb(self, limit: int) -> Optional[list[dict]]:
        import psycopg
        try:
            with psycopg.connect(self.tiger_url, connect_timeout=3) as conn:  # type: ignore[arg-type]
                # The time_to_brief view of the Tiger schema in services/agents/db.py (seconds of agent work to brief v1).
                cur = conn.execute(
                    "SELECT ts, incident_id, seconds, prefetched, fallback FROM time_to_brief "
                    "ORDER BY ts DESC LIMIT %s", (limit,))
                return [{"ts": ts.isoformat(), "incident_id": i, "ms": round(s * 1000, 1), "cached_dossier": bool(p),
                         "fallback": bool(f)} for ts, i, s, p, f in reversed(cur.fetchall())]
        except Exception as e:  # noqa: BLE001
            log.warning("tiger read failed, using workspace metrics: %s", e)
            return None
