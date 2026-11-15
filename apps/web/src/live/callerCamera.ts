// The caller's phone camera as a sensor, on the page the texted camera link opens (CameraLinkPage): it streams
// into the camera call the link belongs to (cam1, ...), one JPEG at a time to
// POST /api/incidents/{id}/camera/{cam}/frames. The server reads it (room, smoke/flames, hazards, people)
// and puts anything new in the transcript like a caller line. The next frame goes after the answer + a pause,
// so a slow vision call never piles up frames.

export interface CameraUpdate {
  status: "starting" | "streaming" | "stopped" | "error";
  callId?: string;
  frames: number;
  observation?: string | null;     // the latest new observation (null = the last frame added nothing)
  summary?: string | null;         // the model's sentence for the last frame
  error?: string;
}

const FRAME_GAP_MS = 1500;   // a reading takes ~5 s, so ~1 frame every 6-7 s
const MAX_WIDTH = 960;

export function cameraProblem(): string | null {
  if (!window.isSecureContext) return "The camera needs HTTPS (or localhost).";
  if (!navigator.mediaDevices?.getUserMedia) return "This browser has no camera access.";
  return null;
}

export class CallerCamera {
  readonly video: HTMLVideoElement;
  private canvas = document.createElement("canvas");
  private stopped = false;
  private incidentId: string | null = null;
  private callId: string | null = null;
  private frames = 0;

  private constructor(readonly stream: MediaStream, private onUpdate: (u: CameraUpdate) => void) {
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;      // iOS: no full-screen takeover
    this.video.srcObject = stream;
    void this.video.play().catch(() => {});
  }

  /** Ask for the camera (back camera on phones). null when there is none or it was refused: the call goes on without it. */
  static async open(onUpdate: (u: CameraUpdate) => void): Promise<CallerCamera | null> {
    if (cameraProblem()) return null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false,
      });
      return new CallerCamera(stream, onUpdate);
    } catch {
      return null;
    }
  }

  /** Stream into a camera call that already exists (the caller opened a texted link). */
  streamInto(incidentId: string, callId: string) {
    if (this.stopped) return;
    this.incidentId = incidentId;
    this.callId = callId;
    this.emit({ status: "streaming" });
    void this.loop();
  }

  private emit(u: Partial<CameraUpdate> & { status: CameraUpdate["status"] }) {
    this.onUpdate({ frames: this.frames, callId: this.callId ?? undefined, ...u });
  }

  private grab(): Promise<Blob | null> {
    const v = this.video;
    if (!v.videoWidth) return Promise.resolve(null);
    const scale = Math.min(1, MAX_WIDTH / v.videoWidth);
    this.canvas.width = Math.round(v.videoWidth * scale);
    this.canvas.height = Math.round(v.videoHeight * scale);
    this.canvas.getContext("2d")?.drawImage(v, 0, 0, this.canvas.width, this.canvas.height);
    return new Promise((ok) => this.canvas.toBlob(ok, "image/jpeg", 0.75));
  }

  private async loop() {
    const url = `/api/incidents/${this.incidentId}/camera/${this.callId}/frames`;
    while (!this.stopped) {
      const jpeg = await this.grab();
      if (jpeg) {
        try {
          const r = await fetch(url, { method: "POST", headers: { "content-type": "image/jpeg" }, body: jpeg });
          if (r.status === 404) return this.stop(false);   // cleared on another screen
          if (r.ok) {
            const out = (await r.json()) as { observation: string | null; reading: { summary?: string } | null; camera: { frames: number } };
            this.frames = out.camera.frames;
            if (!this.stopped) this.emit({ status: "streaming", observation: out.observation, summary: out.reading?.summary ?? null });
          }
        } catch { /* offline for a moment: try the next frame */ }
      }
      await new Promise((ok) => window.setTimeout(ok, FRAME_GAP_MS));
    }
  }

  /** Camera off. `notify`: tell the server the camera call ended. `quiet`: no "stopped" update. */
  stop(notify = true, quiet = false) {
    if (this.stopped) return;
    this.stopped = true;
    this.stream.getTracks().forEach((t) => t.stop());
    if (notify && this.incidentId && this.callId) {
      void fetch(`/api/incidents/${this.incidentId}/camera/${this.callId}/end`, { method: "POST" }).catch(() => {});
    }
    if (!quiet) this.emit({ status: "stopped" });
  }
}
