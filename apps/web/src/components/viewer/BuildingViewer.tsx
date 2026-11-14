import { useEffect, useRef, useState } from "react";

// Static house viewer from public/house-viewer, embedded in an iframe (same origin).
// layout "split" -> 3D + 2D plan side by side in one iframe; otherwise mode "floor" (2D) or "scan3d" (3D).
// The viewer reports "ready" / "error" via postMessage.
// The MDT shows the incident building directly (sampleId); the temporary sample-house picker lives in the
// Dispatcher view's Demo / Controls section and keeps its choice in ?sample=<id>.
/** Incident state reported by the viewer (validated by the host before use). */
export type ViewerIncidentState = { overlay: Record<string, unknown> | null; version: number | null; versions: number[]; routes: Record<string, unknown>[]; noRoute: string[]; plannerMs: number | null };
type Props = {
  mode?: "floor" | "scan3d"; layout?: "single" | "split"; sampleId: string | null; sampleError?: string | null;
  /** inside the MDT house view: the host shows the considerations panel (the viewer hides its own) */
  host?: boolean;
  onIncidentState?: (s: ViewerIncidentState) => void;
  onPlanIndex?: (index: unknown) => void;
  /** overlay ids to highlight in the viewer (a consideration's refs) */
  highlightRefs?: string[];
};

/** House viewer in the MDT by default (true); ?viewer=0 shows the previous Map / Floor 2D / 3D scan tabs. */
export const HOUSE_VIEWER_DEFAULT = true;
type Status = { kind: "idle" } | { kind: "loading" } | { kind: "ready" } | { kind: "error"; message: string };
export type SampleHouse = {
  id: string; address: string; city?: string; matterportModelId: string | null; no_scan?: boolean; location?: { lat: number; lon: number };
  listing?: { beds?: number; baths?: number; sqft?: number; yearBuilt?: number; homeType?: string };
};
const facts = (s: SampleHouse) => [s.no_scan && "no interior scan", s.listing?.beds != null && `${s.listing.beds} bd`, s.listing?.sqft && `${s.listing.sqft.toLocaleString("en-US")} sq ft`].filter(Boolean).join(" · ");

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

export const readSampleParam = () => {
  const id = new URLSearchParams(window.location.search).get("sample");
  return validId(id) ? id : null;
};

export function setSampleParam(id: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("sample", id);
  window.history.replaceState(window.history.state, "", url);
}

/** Sample houses with a Matterport scan (public/house-viewer/samples/index.json), fetched once. */
export function useSampleHouses() {
  const [samples, setSamples] = useState<SampleHouse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`${BASE}samples/index.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: SampleHouse[]) => alive && setSamples(list))
      .catch(() => alive && setError("Sample list could not be loaded"));
    return () => { alive = false; };
  }, []);
  return { samples, error };
}

/** Compact temporary picker: chip with the current house, popover with a text search and the list. */
export function SamplePicker({ samples, value, onChange }: { samples: SampleHouse[]; value: string | null; onChange: (id: string) => void }) {
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

export function BuildingViewer({ mode = "scan3d", layout = "single", sampleId, sampleError, host = false, onIncidentState, onPlanIndex, highlightRefs }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  // Sample in the iframe URL: fixed once the iframe exists; later changes go by postMessage (no reload).
  const [frameSample, setFrameSample] = useState<string | null>(sampleId);
  const loaded = useRef<string | null>(sampleId);
  const [status, setStatus] = useState<Status>(sampleId ? { kind: "loading" } : { kind: "idle" });
  const [loadSeq, setLoadSeq] = useState(0);

  const split = layout === "split";
  const view = split ? "layout=split" : `mode=${mode === "floor" ? "2d" : "3d"}`;
  // ?debug=1 (and the optional scenario / v) on the page is forwarded to the viewer (incident scenario selector)
  const page = new URLSearchParams(window.location.search);
  const debug = page.get("debug") === "1"
    ? "&debug=1" + ["scenario", "v"].filter((k) => /^[A-Za-z0-9]{1,8}$/.test(page.get(k) ?? "")).map((k) => `&${k}=${page.get(k)}`).join("")
    : "";
  const src = frameSample ? `${BASE}casa.html?embed=1&sample=${encodeURIComponent(frameSample)}&${view}${debug}${host ? "&host=1" : ""}` : null;
  const cb = useRef({ onIncidentState, onPlanIndex });
  cb.current = { onIncidentState, onPlanIndex };
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
      const d = e.data as ({ type?: string; message?: string; version?: number; errors?: string[]; index?: unknown } & Partial<ViewerIncidentState>) | null;
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
      else if (d?.type === "planIndex") { planIndex.current = d.index ?? null; cb.current.onPlanIndex?.(d.index ?? null); }
      else if (d?.type === "incidentState" && cb.current.onIncidentState)
        cb.current.onIncidentState({ overlay: (d.overlay as ViewerIncidentState["overlay"]) ?? null, version: d.version ?? null, versions: Array.isArray(d.versions) ? d.versions : [],
          routes: Array.isArray(d.routes) ? d.routes : [], noRoute: Array.isArray(d.noRoute) ? d.noRoute : [], plannerMs: typeof d.plannerMs === "number" ? d.plannerMs : null });
    };
    window.addEventListener("message", onMessage);
    return () => { window.removeEventListener("message", onMessage); window.clearTimeout(themeTimer.current); };
  }, []);

  // highlight a consideration's refs in the viewer
  const refsKey = JSON.stringify(highlightRefs ?? []);
  useEffect(() => {
    if (status.kind === "ready") frame.current?.contentWindow?.postMessage({ type: "highlightRefs", refs: JSON.parse(refsKey) }, window.location.origin);
  }, [refsKey, status.kind]);

  useEffect(() => {
    if (status.kind !== "loading") return;
    const timer = window.setTimeout(() => setStatus((s) => (s.kind === "loading" ? { kind: "error", message: "Viewer did not respond" } : s)), TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [status.kind, loadSeq]);

  // The incident building changed (Demo / Controls picker): load it without reloading the iframe when one exists.
  useEffect(() => {
    if (sampleId === loaded.current) return;
    loaded.current = sampleId;
    if (!sampleId) return setStatus({ kind: "idle" });
    setStatus({ kind: "loading" });
    setLoadSeq((n) => n + 1);
    if (!frame.current?.contentWindow) setFrameSample(sampleId);
    else frame.current.contentWindow.postMessage({ type: "loadSample", id: sampleId }, window.location.origin);
  }, [sampleId]);

  const overlay =
    sampleError ? (
      <div>
        <div className="num text-[15px] text-unverified">○ VIEWER UNAVAILABLE</div>
        <div className="mt-1 text-[12px] text-faint">{sampleError}</div>
      </div>
    ) : status.kind === "idle" ? (
      <div className="num text-[13px] text-dim">LOADING BUILDING…</div>
    ) : status.kind === "loading" ? (
      <div className="num text-[13px] text-dim">LOADING {what}…</div>
    ) : status.kind === "error" ? (
      <div>
        <div className="num text-[15px] text-unverified">○ VIEWER UNAVAILABLE</div>
        <div className="mt-1 text-[12px] text-faint">{status.message}</div>
      </div>
    ) : null;

  return (
    <div className="relative h-full w-full bg-bg">
      {src && !sampleError && (
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
  );
}
