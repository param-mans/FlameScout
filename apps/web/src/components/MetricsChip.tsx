import { useEffect, useState } from "react";
import { api, type TimeToBrief } from "../api/client";

const seconds = (ms: number | null | undefined) => (ms == null ? "n/a" : `${(ms / 1000).toFixed(1)} s`);

/** Time from a confirmed address to the first brief, measured by the API and read from Tiger Data (the time_to_brief view).
 *  Facts-only fallbacks are left out of the percentiles. Shows what was measured, nothing else. */
export function MetricsChip() {
  const [m, setM] = useState<TimeToBrief | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.timeToBrief().then((d) => alive && setM(d)).catch(() => alive && setM(null));
    load();
    const t = window.setInterval(load, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  if (!m || m.n === 0) return null;
  const src = m.source === "tiger" ? "TIGER DATA" : "LOCAL";
  const detail = `Time to brief, from ${src}: p50 ${seconds(m.p50_ms)}, p95 ${seconds(m.p95_ms)} over ${m.n} briefs`
    + (m.n_fallback ? ` (${m.n_fallback} facts-only fallbacks not counted)` : "");
  return (
    <span className="num border border-line-hi px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-dim" title={detail}>
      BRIEF P50 {seconds(m.p50_ms)} · P95 {seconds(m.p95_ms)} · {m.n} RUNS · {src}
    </span>
  );
}
