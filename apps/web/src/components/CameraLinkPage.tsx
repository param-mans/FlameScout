// What the caller sees after opening the texted link (/?camera=<token>): one button to turn the camera on, a
// live preview, and what FlameScout has read so far. The frames go into the dispatcher's incident as a camera
// call (cam1, ...). Nothing else of the app is shown on this device.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type CameraInfo } from "../api/client";
import { CallerCamera, cameraProblem, type CameraUpdate } from "../live/callerCamera";

export function CameraLinkPage({ token }: { token: string }) {
  const [info, setInfo] = useState<CameraInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [update, setUpdate] = useState<CameraUpdate | null>(null);
  const [seen, setSeen] = useState<string[]>([]);
  const [cam, setCam] = useState<CallerCamera | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api.cameraByToken(token).then((r) => setInfo(r.camera), (e) =>
      setError(e instanceof ApiError && e.status === 404 ? "This camera link has ended." : String(e)));
  }, [token]);

  useEffect(() => {
    const el = box.current;
    if (!el || !cam) return;
    cam.video.className = "block h-full w-full object-cover";
    el.appendChild(cam.video);
    return () => { cam.video.remove(); };
  }, [cam]);

  useEffect(() => () => cam?.stop(), [cam]);

  const start = async () => {
    if (!info) return;
    setError(null);
    const c = await CallerCamera.open((u) => {
      setUpdate(u);
      if (u.observation) setSeen((s) => [u.observation as string, ...s].slice(0, 5));
    });
    if (!c) { setError(cameraProblem() ?? "The camera is unavailable or access was refused. Allow it in your browser settings and try again."); return; }
    setCam(c);
    c.streamInto(info.incident_id, info.call_id);
  };

  const on = cam && update?.status === "streaming";
  return (
    <div className="flex min-h-full flex-col gap-3 bg-bg p-4 text-ink">
      <div className="flex items-center gap-2 text-[12px] font-bold tracking-[0.18em]">
        <span className="inline-block h-2.5 w-2.5 bg-accent" aria-hidden />FLAMESCOUT
        <span className="num ml-auto text-[10px] font-semibold tracking-wider text-dim">SIMULATION</span>
      </div>
      <p className="text-[15px] leading-snug">
        The dispatcher would like to see what you see. Stay on the phone call and stay safe: if you can, show the fire,
        the smoke, the room you are in, and anything dangerous like gas tanks.
      </p>
      {error && <div className="border border-contradicted/60 bg-contradicted/10 px-3 py-2 text-[13px] text-contradicted">{error}</div>}
      {!cam && info && (
        <button onClick={() => void start()}
          className="border border-accent bg-accent/20 py-4 text-[16px] font-semibold uppercase tracking-wider text-ink">
          📷 Turn on camera
        </button>
      )}
      {cam && (
        <>
          <div ref={box} className="aspect-[3/4] w-full overflow-hidden border border-line-hi bg-panel" />
          <div className="num flex items-center gap-2 text-[12px] text-dim">
            {on && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-contradicted" aria-hidden />}
            {on ? `SHARING WITH DISPATCH · ${update?.frames ?? 0} read` : update?.status === "stopped" ? "CAMERA OFF" : "STARTING…"}
            {on && <button onClick={() => cam.stop()} className="ml-auto border border-line-hi px-2 py-1 text-[11px] uppercase">Stop</button>}
          </div>
          {seen.map((s, i) => <div key={i} className={`text-[13px] ${i ? "text-faint" : "text-ink"}`}>{s}</div>)}
        </>
      )}
    </div>
  );
}
