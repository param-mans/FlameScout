import type { CallSegment, Contradiction } from "../types/schemas";
import { briefIssuedAt } from "../mock/s2";

const END = 28; // seconds shown on the mock axis

const pct = (t: number) => `${(t / END) * 100}%`;

/** Calls, contradictions and brief versions on one axis. Mock timing until live events exist. */
export function Timeline({
  segments, contradictions, currentVersion,
}: {
  segments: CallSegment[];
  contradictions: Contradiction[];
  currentVersion: number;
}) {
  const calls = [...new Set(segments.map((s) => s.call_id))];
  const track = "relative h-4 border-b border-line";
  return (
    <div className="grid grid-cols-[64px_1fr] border-t border-line bg-panel text-[10px]">
      {calls.map((c) => (
        <div key={c} className="contents">
          <div className="label border-b border-line px-2.5 leading-4">{c.toUpperCase()}</div>
          <div className={track}>
            {segments.filter((s) => s.call_id === c).map((s, i) => (
              <div
                key={i}
                title={s.text}
                className={`absolute top-[5px] h-1.5 ${s.speaker === "caller" ? "bg-ink/70" : "bg-line-hi"}`}
                style={{ left: pct(s.t_start), width: `calc(${pct(s.t_end - s.t_start)} - 1px)` }}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="label border-b border-line px-2.5 leading-4">Events</div>
      <div className={track}>
        {contradictions.map((c) =>
          c.values.slice(1, 2).map((v) => (
            <span key={c.key} className="num absolute top-0 whitespace-nowrap text-contradicted" style={{ left: pct(v.span.start) }} title={`Contradiction: ${c.key}`}>
              ▲ {c.key}
            </span>
          )),
        )}
      </div>
      <div className="label px-2.5 leading-4">Brief</div>
      <div className="relative h-4">
        {Object.entries(briefIssuedAt).map(([v, t]) => (
          <span
            key={v}
            className={`num absolute top-0 whitespace-nowrap border-l px-1 ${Number(v) === currentVersion ? "border-accent text-accent" : "border-line-hi text-dim"}`}
            style={{ left: pct(t) }}
          >
            v{v}
          </span>
        ))}
      </div>
    </div>
  );
}
