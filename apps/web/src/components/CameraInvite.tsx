// Dispatcher: ask the caller for their camera. Sends them a link by iMessage (Photon Stable, when configured) and
// always shows the link to share by hand. The camera call's status follows camera.status events (a link sent
// automatically when the call started shows up here too).
import { useState } from "react";
import type { CameraInfo, CameraMessage } from "../api/client";

export interface InviteResult { url: string; message: CameraMessage; camera: CameraInfo }

export function CameraInvite({ cameras, onInvite }: {
  cameras: Record<string, CameraInfo>;
  onInvite: (phone: string | null) => Promise<InviteResult>;
}) {
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<InviteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const send = async () => {
    setBusy(true); setError(null); setCopied(false);
    try { setRes(await onInvite(phone.trim() || null)); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const copy = async () => {
    if (!res) return;
    try { await navigator.clipboard.writeText(res.url); setCopied(true); } catch { /* not allowed: the link is selectable */ }
  };

  const live = Object.values(cameras).filter((c) => c.status !== "ended");
  return (
    <div className="border-t border-line bg-panel px-2.5 py-2 text-[12px]">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="num text-[11px] font-semibold tracking-wider text-dim">CALLER CAMERA</span>
        <input value={phone} onChange={(e) => setPhone(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void send(); }}
          type="tel" inputMode="tel" placeholder="Caller's number (optional)" aria-label="Caller's phone number"
          className="num min-w-0 flex-1 border border-line-hi bg-bg px-2 py-0.5 text-ink outline-none placeholder:text-faint focus:border-accent" />
        <button onClick={() => void send()} disabled={busy}
          className="border border-accent/70 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink hover:bg-accent/15 disabled:opacity-50">
          {busy ? "Sending…" : phone.trim() ? "📷 iMessage link" : "📷 Get link"}
        </button>
      </div>
      {error && <div className="mt-1 text-contradicted">{error}</div>}
      {res && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {!cameras[res.camera.call_id]?.invite && <span className="text-dim">{res.message.detail}</span>}
          <a href={res.url} target="_blank" rel="noreferrer" className="num min-w-0 truncate text-accent underline">{res.url}</a>
          <button onClick={() => void copy()} className="border border-line-hi px-1.5 text-[10px] uppercase text-dim hover:bg-accent/15">
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
      {live.map((c) => (
        <div key={c.call_id} className="num mt-1 text-[11px] text-dim">
          {c.call_id.toUpperCase()} · {c.status === "invited" ? `waiting for the caller to open the link${c.sent_to ? ` (${c.sent_to})` : ""}` : `LIVE · ${c.frames} frames read · ${c.observations} new`}
          {c.status === "invited" && c.invite && c.invite.state !== "none" && (
            <span className={`block ${c.invite.state === "sent" ? "text-verified" : c.invite.state === "failed" ? "text-contradicted" : "text-dim"}`}>
              {c.invite.state === "sent" ? "✓ " : ""}{c.invite.detail}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
