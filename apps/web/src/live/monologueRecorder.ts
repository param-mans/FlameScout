// Records the whole call-in monologue as one 16 kHz mono PCM16 WAV (services/api/transcribe.py transcribes it
// in one go). No voice detection: everything between Call in and Done is the call. Stops itself at MAX_SECONDS.

export const RATE = 16000;
export const MAX_SECONDS = 300;      // the server takes 5 min at most

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("fs-monologue-tap", Tap);
`;

type AudioCtor = typeof AudioContext;
const AudioCtx: AudioCtor | undefined =
  window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioCtor }).webkitAudioContext;

/** Why this page can't record, or null. Browsers only give the mic (and AudioWorklet) to secure pages:
 *  https:// or localhost. A phone on http://<laptop-ip> gets neither ("undefined is not an object"). */
export function micProblem(): string | null {
  if (!window.isSecureContext) {
    return `The mic needs a secure page. Open this site over https:// (e.g. an ngrok URL), not ${window.location.origin}.`;
  }
  if (!navigator.mediaDevices?.getUserMedia) return "This browser can't use the microphone.";
  if (!AudioCtx || !("audioWorklet" in AudioCtx.prototype)) return "This browser is too old for recording (no AudioWorklet).";
  return null;
}

function wavBlob(chunks: Float32Array[]): Blob {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, RATE, true); v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, c[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

export class MonologueRecorder {
  private ctx: AudioContext;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private chunks: Float32Array[] = [];
  private samples = 0;
  private stopped = false;

  // Create inside the click handler: the AudioContext needs a user gesture.
  constructor(private onLevel: (rms: number, seconds: number) => void, private onFull: () => void) {
    this.ctx = new (AudioCtx as AudioCtor)({ sampleRate: RATE });
  }

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    await this.ctx.resume();
    this.node = new AudioWorkletNode(this.ctx, "fs-monologue-tap");
    let meter = 0;
    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => {
      if (this.stopped) return;
      const f = e.data;
      this.chunks.push(f);
      this.samples += f.length;
      if ((meter += f.length) >= RATE / 10) {      // ~10 level updates a second
        meter = 0;
        let s = 0;
        for (let i = 0; i < f.length; i++) s += f[i] * f[i];
        this.onLevel(Math.sqrt(s / f.length), this.samples / RATE);
      }
      if (this.samples >= MAX_SECONDS * RATE) this.onFull();
    };
    this.ctx.createMediaStreamSource(this.stream).connect(this.node);
  }

  /** Stop the mic. With keep, returns the recording as a WAV; otherwise drops it. */
  stop(keep = true): Blob | null {
    if (this.stopped) return null;
    this.stopped = true;
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx.close().catch(() => {});
    const wav = keep && this.samples ? wavBlob(this.chunks) : null;
    this.chunks = [];
    return wav;
  }
}
