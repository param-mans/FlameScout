import type { ReactNode } from "react";
import type { FactStatus } from "../types/schemas";

// Shape + code + color: status is never color alone (PRD section 10).
const STATUS: Record<FactStatus, { glyph: string; code: string; text: string; color: string }> = {
  verified: { glyph: "●", code: "VER", text: "Verified", color: "text-verified" },
  inferred: { glyph: "◆", code: "INF", text: "Inferred", color: "text-inferred" },
  reported: { glyph: "■", code: "RPT", text: "Reported", color: "text-reported" },
  unverified: { glyph: "○", code: "UNV", text: "Unverified", color: "text-unverified" },
  contradicted: { glyph: "▲", code: "CON", text: "Contradicted", color: "text-contradicted" },
};

export const statusText = (s: FactStatus) => STATUS[s].text;
export const statusColorVar = (s: FactStatus) => `var(--color-${s})`;

export function StatusTag({ status, confidence }: { status: FactStatus; confidence?: number }) {
  const s = STATUS[status];
  return (
    <span className={`num inline-flex items-center gap-1 text-[11px] font-semibold ${s.color}`} title={s.text}>
      <span aria-hidden>{s.glyph}</span>
      {s.code}
      {confidence !== undefined && <span className="text-dim">{Math.round(confidence * 100)}</span>}
    </span>
  );
}

export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`flex min-h-0 flex-col bg-panel ${className}`}>
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-line px-2.5">
        <h2 className="label">{title}</h2>
        <div className="num text-[11px] text-dim">{right}</div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}

/** Dense key/value row: label left, mono value right, status at far right. */
export function KV({
  k,
  v,
  status,
  confidence,
  selected,
  onClick,
}: {
  k: string;
  v: ReactNode;
  status?: FactStatus;
  confidence?: number;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`grid w-full grid-cols-[76px_1fr_auto] items-baseline gap-2 border-b border-line px-2.5 py-1.5 text-left hover:bg-panel-hi ${
        selected ? "bg-panel-hi shadow-[inset_2px_0_0_var(--color-accent)]" : ""
      }`}
    >
      <span className="label !normal-case !tracking-normal">{k}</span>
      <span className="num break-words text-[13px] leading-snug text-ink">{v}</span>
      {status ? <StatusTag status={status} confidence={confidence} /> : <span />}
    </button>
  );
}

export function Btn({ children, onClick, active }: { children: ReactNode; onClick?: () => void; active?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${
        active ? "border-accent bg-accent/15 text-accent" : "border-line-hi text-dim hover:bg-panel-hi hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

export const fmt = (v: unknown) => (v === null || v === undefined ? "—" : String(v));
