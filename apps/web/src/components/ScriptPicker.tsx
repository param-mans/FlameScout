// Scripted-call picker for the nav: SHOWCASE scripts first, then TEST, each with the manifest title, a group badge and
// an INCONSISTENT badge when the callers contradict each other. Works with older APIs that send only script_id,
// description and segments (one plain list). "" = blank incident to type into.
import { useEffect, useRef, useState } from "react";
import type { ScriptInfo } from "../api/client";

const GROUPS = [
  { key: "showcase", label: "SHOWCASE" },
  { key: "test", label: "TEST" },
] as const;

/** "SHOWCASE · 9660 Fairdale Lane" -> "9660 Fairdale Lane" (the badges say the rest). */
export function scriptTitle(s: ScriptInfo): string {
  const t = (s.title ?? "").replace(/^(SHOWCASE|TEST)\s*·\s*/i, "").replace(/^INCONSISTENT\s*·\s*/i, "").trim();
  return t || s.script_id;
}

/** Showcase first (API order inside a group), then test, then anything without a known group. */
export function orderScripts(scripts: ScriptInfo[]): ScriptInfo[] {
  const rank = (s: ScriptInfo) => { const i = GROUPS.findIndex((g) => g.key === s.group); return i < 0 ? GROUPS.length : i; };
  return scripts.map((s, i) => ({ s, i })).sort((a, b) => rank(a.s) - rank(b.s) || a.i - b.i).map((x) => x.s);
}

/** Default selection: the first showcase script, else the first script, else blank. */
export const defaultScript = (scripts: ScriptInfo[]): string => orderScripts(scripts)[0]?.script_id ?? "";

function Badge({ kind }: { kind: "showcase" | "test" | "inconsistent" }) {
  const cls = kind === "inconsistent" ? "border-contradicted bg-contradicted/20 text-contradicted"
    : kind === "showcase" ? "border-accent/70 text-accent" : "border-line-hi text-dim";
  return <span data-badge={kind} className={`num shrink-0 border px-1 text-[10px] font-semibold leading-4 tracking-wider ${cls}`}>{kind.toUpperCase()}</span>;
}

export function ScriptPicker({ scripts, value, onChange }: { scripts: ScriptInfo[]; value: string; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", esc); };
  }, [open]);

  const ordered = orderScripts(scripts);
  const grouped = ordered.some((s) => s.group);
  const sections = grouped
    ? [...GROUPS.map((g) => ({ label: g.label as string | null, items: ordered.filter((s) => s.group === g.key) })),
       { label: "OTHER", items: ordered.filter((s) => !GROUPS.some((g) => g.key === s.group)) }]
    : [{ label: null, items: ordered }];
  const current = scripts.find((s) => s.script_id === value) ?? null;
  const pick = (id: string) => { onChange(id); setOpen(false); };

  const row = (s: ScriptInfo) => (
    <li key={s.script_id}>
      <button
        type="button" role="option" aria-selected={s.script_id === value} data-script={s.script_id} onClick={() => pick(s.script_id)}
        className={`flex min-h-[44px] w-full items-center gap-2 border-b border-line px-2.5 py-1.5 text-left hover:bg-panel-hi ${
          s.script_id === value ? "bg-panel-hi shadow-[inset_2px_0_0_var(--color-accent)]" : ""}`}
      >
        {s.group === "showcase" || s.group === "test" ? <Badge kind={s.group} /> : null}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] text-ink">{scriptTitle(s)}</span>
          <span className="num block text-[11px] text-faint">{s.script_id} · {s.segments} segments</span>
        </span>
        {s.inconsistent && <Badge kind="inconsistent" />}
      </button>
    </li>
  );

  return (
    <div ref={box} className="relative">
      <button
        type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open} aria-label="Scripted call" data-script-picker
        className="num relative flex h-7 max-w-[340px] items-center gap-1.5 border border-line-hi bg-bg px-1.5 text-[11px] text-ink after:absolute after:inset-x-0 after:-inset-y-2 after:content-['']"
      >
        {current?.group === "showcase" || current?.group === "test" ? <Badge kind={current.group} /> : null}
        <span className="truncate">{current ? scriptTitle(current) : "Blank · type your own"}</span>
        {current?.inconsistent && <Badge kind="inconsistent" />}
        <span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-[400px] border border-line-hi bg-panel shadow-[0_8px_24px_rgba(0,0,0,0.5)]">
          <ul role="listbox" aria-label="Scripted calls" className="max-h-[70vh] overflow-y-auto">
            {sections.filter((x) => x.items.length).map((x) => (
              <li key={x.label ?? "all"} role="presentation">
                {x.label && <div className="label border-b border-line bg-bg px-2.5 py-1" data-group-header={x.label}>{x.label}</div>}
                <ul role="presentation">{x.items.map(row)}</ul>
              </li>
            ))}
            <li>
              <button type="button" role="option" aria-selected={value === ""} data-script="" onClick={() => pick("")}
                className={`flex min-h-[44px] w-full items-center px-2.5 text-left text-[13px] text-dim hover:bg-panel-hi ${value === "" ? "bg-panel-hi" : ""}`}>
                Blank · type your own
              </button>
            </li>
          </ul>
        </div>
      )}
    </div>
  );
}
