import { useEffect, useRef, useState } from "react";

// Static house viewer from public/house-viewer, embedded in an iframe (same origin).
// mode "floor" -> 2D plan, "scan3d" -> 3D model. The viewer reports "ready" / "error" via postMessage.
type Props = { mode: "floor" | "scan3d"; sampleId?: string };
type Status = { kind: "loading" } | { kind: "ready" } | { kind: "error"; message: string };

const TIMEOUT_MS = 15000;

export function BuildingViewer({ mode, sampleId = "sample-a" }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const sample = /^[a-z0-9-]+$/.test(sampleId) ? sampleId : "sample-a";
  const src = `${import.meta.env.BASE_URL}house-viewer/casa.html?embed=1&sample=${encodeURIComponent(sample)}&mode=${mode === "floor" ? "2d" : "3d"}`;

  useEffect(() => {
    setStatus({ kind: "loading" });
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin || e.source !== frame.current?.contentWindow) return;
      const d = e.data as { type?: string; message?: string } | null;
      if (d?.type === "ready") setStatus({ kind: "ready" });
      else if (d?.type === "error") setStatus({ kind: "error", message: d.message ?? "Viewer error" });
    };
    window.addEventListener("message", onMessage);
    const timer = window.setTimeout(
      () => setStatus((s) => (s.kind === "loading" ? { kind: "error", message: "Viewer did not respond" } : s)),
      TIMEOUT_MS,
    );
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };
  }, [src]);

  return (
    <div className="relative h-full w-full bg-bg">
      <iframe
        ref={frame}
        key={src}
        src={src}
        title={mode === "floor" ? "Floor plan 2D" : "3D scan"}
        className={`absolute inset-0 h-full w-full border-0 bg-bg ${status.kind === "ready" ? "" : "invisible"}`}
        onError={() => setStatus({ kind: "error", message: "Viewer could not be loaded" })}
      />
      {status.kind !== "ready" && (
        <div className="absolute inset-0 grid place-items-center text-center">
          {status.kind === "loading" ? (
            <div className="num text-[13px] text-dim">LOADING {mode === "floor" ? "FLOOR PLAN" : "3D SCAN"}…</div>
          ) : (
            <div>
              <div className="num text-[15px] text-unverified">○ VIEWER UNAVAILABLE</div>
              <div className="mt-1 text-[12px] text-faint">{status.message}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
