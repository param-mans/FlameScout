import { useEffect, useMemo, useRef, useState } from "react";

// Static house viewer from public/house-viewer, embedded in an iframe (same origin).
// layout "split" -> 3D + 2D plan side by side in one iframe; otherwise mode "floor" (2D) or "scan3d" (3D).
// The viewer reports "ready" / "error" via postMessage.
// The sample house is chosen with the temporary picker on top; the choice is kept in ?sample=<id>.
type Props = { mode?: "floor" | "scan3d"; layout?: "single" | "split"; sampleId?: string };

/** House viewer in the MDT by default (true); ?viewer=0 shows the previous Map / Floor 2D / 3D scan tabs. */
export const HOUSE_VIEWER_DEFAULT = true;
type Status = { kind: "idle" } | { kind: "loading" } | { kind: "ready" } | { kind: "error"; message: string };
export type SampleHouse = {
  id: string; address: string; city?: string; matterportModelId: string; location?: { lat: number; lon: number };
  listing?: { beds?: number; baths?: number; sqft?: number; yearBuilt?: number; homeType?: string };
};
const facts = (s: SampleHouse) => [s.listing?.beds != null && `${s.listing.beds} bd`, s.listing?.sqft && `${s.listing.sqft.toLocaleString("en-US")} sq ft`].filter(Boolean).join(" · ");

const BASE = `${import.meta.env.BASE_URL}house-viewer/`;
const TIMEOUT_MS = 15000;
const THEME_WAIT_MS = 1500;

// Design tokens of the app (src/index.css @theme) sent to the viewer so it looks like part of the UI.
const THEME_TOKENS = [
  "--color-bg", "--color-panel", "--color-panel-hi", "--color-line", "--color-line-hi", "--color-ink", "--color-dim", "--color-faint",
  "--color-accent", "--color-verified", "--color-inferred", "--color-unverified", "--font-mono", "--font-sans",
];
function readThemeTokens(): Record<string, string> {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(THEME_TOKENS.map((k) => [k, css.getPropertyValue(k).trim()]).filter(([, v]) => v));
}
const validId = (id: string | null | undefined): id is string => !!id && /^[a-z0-9-]+$/.test(id);

function setSampleParam(id: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("sample", id);
  window.history.replaceState(window.history.state, "", url);
}

/** Compact temporary picker: chip with the current house, popover with a text search and the list. */
function SamplePicker({ samples, value, onChange }: { samples: SampleHouse[]; value: string | null; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const current = samples.find((s) => s.id === value) ?? null;
  const q = query.trim().toLowerCase();
  const shown = samples.filter((s) => !q || `${s.address} ${s.city ?? ""} ${s.id}`.toLowerCase().includes(q));

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);

  const pick = (id: string) => { onChange(id); setOpen(false); setQuery(""); };

  return (
    <div ref={box} className="relative flex items-center gap-2">
      <span className="label">Sample house <span className="!normal-case !tracking-normal text-faint">· temporary</span></span>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`num flex max-w-[360px] items-center gap-2 border px-2 py-0.5 text-[11px] ${
          current ? "border-accent bg-accent/15 text-accent" : "border-line-hi text-dim hover:bg-panel-hi hover:text-ink"
        }`}
      >
        <span className="truncate">{current ? [current.address, facts(current)].filter(Boolean).join(" · ") : "Select a sample house"}</span>
        <span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-[380px] border border-line-hi bg-panel shadow-[0_8px_24px_rgba(0,0,0,0.5)]">
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && shown.length) pick(shown[0].id); }}
            placeholder="Search address or city…"
            aria-label="Search sample houses"
            className="num w-full border-b border-line bg-bg px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-faint"
          />
          <ul role="listbox" className="max-h-64 overflow-y-auto">
            {shown.length === 0 && <li className="px-2.5 py-2 text-[12px] text-dim">No sample house matches.</li>}
            {shown.map((s) => (
              <li key={s.id}>
                <button
                  role="option"
                  aria-selected={s.id === value}
                  onClick={() => pick(s.id)}
                  className={`grid w-full grid-cols-[1fr_auto] gap-2 border-b border-line px-2.5 py-1.5 text-left hover:bg-panel-hi ${
                    s.id === value ? "bg-panel-hi shadow-[inset_2px_0_0_var(--color-accent)]" : ""
                  }`}
                >
                  <span className="text-[13px] text-ink">{s.address}</span>
                  <span className="num text-[11px] text-dim">{facts(s)}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="px-2.5 py-1 text-[11px] text-faint">Sample houses with a Matterport scan. Temporary picker.</div>
        </div>
      )}
    </div>
  );
}

export function BuildingViewer({ mode = "scan3d", layout = "single", sampleId }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const initial = useMemo(() => {
    const id = sampleId ?? new URLSearchParams(window.location.search).get("sample");
    return validId(id) ? id : null;
  }, [sampleId]);
  const [samples, setSamples] = useState<SampleHouse[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [sample, setSample] = useState<string | null>(initial);
  // Sample in the iframe URL: fixed once the iframe exists; later choices go by postMessage (no reload).
  const [frameSample, setFrameSample] = useState<string | null>(initial);
  const [status, setStatus] = useState<Status>(initial ? { kind: "loading" } : { kind: "idle" });
  const [loadSeq, setLoadSeq] = useState(0);

  useEffect(() => {
    let alive = true;
    fetch(`${BASE}samples/index.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: SampleHouse[]) => alive && setSamples(list))
      .catch(() => alive && setListError("Sample list could not be loaded"));
    return () => { alive = false; };
  }, []);

  const known = !sample || !samples || samples.some((s) => s.id === sample);
  const split = layout === "split";
  const view = split ? "layout=split" : `mode=${mode === "floor" ? "2d" : "3d"}`;
  const src = frameSample ? `${BASE}casa.html?embed=1&sample=${encodeURIComponent(frameSample)}&${view}` : null;
  const what = split ? "FLOOR PLAN + 3D" : mode === "floor" ? "FLOOR PLAN" : "3D SCAN";

  // Viewer replies from our own iframe only. On the first "ready" of each iframe document the app theme is sent and
  // the iframe stays hidden until "themeApplied" (or a short fallback), so the default viewer theme never flashes.
  const themed = useRef(false);
  const planIndex = useRef<unknown>(null); // latest plan index sent by the viewer (for the incident agents)
  const themeTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    const show = () => { window.clearTimeout(themeTimer.current); setStatus((s) => (s.kind === "loading" ? { kind: "ready" } : s)); };
    const onMessage = (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (e.origin !== window.location.origin || !win || e.source !== win) return;
      const d = e.data as { type?: string; message?: string; version?: number; errors?: string[]; index?: unknown } | null;
      if (d?.type === "ready") {
        if (themed.current) return show();
        win.postMessage({ type: "theme", vars: readThemeTokens() }, window.location.origin);
        window.clearTimeout(themeTimer.current);
        themeTimer.current = window.setTimeout(show, THEME_WAIT_MS);
      } else if (d?.type === "themeApplied") {
        themed.current = true;
        show();
      } else if (d?.type === "error") setStatus({ kind: "error", message: d.message ?? "Viewer error" });
      // incident layer (docs/INCIDENT_OVERLAY_SPEC.md): the host logs rejected overlays; the plan index is kept for the agents
      else if (d?.type === "incidentRejected") console.warn("[house-viewer] incident overlay rejected", { version: d.version, errors: d.errors });
      else if (d?.type === "planIndex") planIndex.current = d.index ?? null;
    };
    window.addEventListener("message", onMessage);
    return () => { window.removeEventListener("message", onMessage); window.clearTimeout(themeTimer.current); };
  }, []);

  useEffect(() => {
    if (status.kind !== "loading") return;
    const timer = window.setTimeout(() => setStatus((s) => (s.kind === "loading" ? { kind: "error", message: "Viewer did not respond" } : s)), TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [status.kind, loadSeq]);

  const choose = (id: string) => {
    if (id === sample) return;
    setSample(id);
    setSampleParam(id);
    setStatus({ kind: "loading" });
    setLoadSeq((n) => n + 1);
    if (!frame.current?.contentWindow) setFrameSample(id);
    else frame.current.contentWindow.postMessage({ type: "loadSample", id }, window.location.origin);
  };

  const overlay =
    status.kind === "idle" || !known ? (
      <div>
        <div className="num text-[15px] text-dim">SELECT A SAMPLE HOUSE</div>
        <div className="mt-1 text-[12px] text-faint">{!known ? `Unknown sample "${sample}". Pick one from the list above.` : "Choose one from the picker above to load its floor plan and 3D model."}</div>
      </div>
    ) : status.kind === "loading" ? (
      <div className="num text-[13px] text-dim">LOADING {what}…</div>
    ) : status.kind === "error" ? (
      <div>
        <div className="num text-[15px] text-unverified">○ VIEWER UNAVAILABLE</div>
        <div className="mt-1 text-[12px] text-faint">{status.message}</div>
      </div>
    ) : null;

  return (
    <div className="flex h-full w-full flex-col bg-bg">
      <div className="flex h-8 shrink-0 items-center gap-3 border-b border-line bg-panel px-2.5">
        {samples ? (
          <SamplePicker samples={samples} value={known ? sample : null} onChange={choose} />
        ) : (
          <span className="label">{listError ?? "Loading sample houses…"}</span>
        )}
      </div>
      <div className="relative min-h-0 flex-1">
        {src && known && (
          <iframe
            ref={frame}
            src={src}
            title={split ? "Floor plan and 3D model" : mode === "floor" ? "Floor plan 2D" : "3D scan"}
            className={`absolute inset-0 h-full w-full border-0 bg-bg ${status.kind === "ready" ? "" : "invisible"}`}
            onError={() => setStatus({ kind: "error", message: "Viewer could not be loaded" })}
          />
        )}
        {overlay && <div className="absolute inset-0 grid place-items-center text-center">{overlay}</div>}
      </div>
    </div>
  );
}
