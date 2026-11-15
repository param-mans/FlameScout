// FlameScout messenger: sends iMessages through Photon Stable (spectrum-ts, Spectrum Cloud lines) for the Python
// API, which has no spectrum-ts. SIMULATION ONLY: texts consenting demo phones, never an emergency line.
//
//   POST /send {to: "+16095550123", text}  ->  200 {ok: true, line}  |  4xx/5xx {ok: false, error}
//   GET  /health                            ->  {ok, configured}
//
// Env: PHOTON_PROJECT_ID + PHOTON_PROJECT_SECRET (PHOTON_API_KEY is read as the secret too). MESSENGER_PORT
// (default 8090), MESSENGER_HOST (default 127.0.0.1; 0.0.0.0 in Docker, where only the compose network reaches it).
// Unlike Photon 2.0's shared lines, Stable can start a DM: im.user(phone) -> im.space.create(user) -> send.
// Quota (Photon docs): 50 new conversations per line per day.
import http from "node:http";
import { Spectrum } from "@spectrum-ts/core";
import { imessage } from "@spectrum-ts/imessage";

const PORT = Number(process.env.MESSENGER_PORT || 8090);
const HOST = process.env.MESSENGER_HOST || "127.0.0.1";
const projectId = (process.env.PHOTON_PROJECT_ID || "").trim();
const projectSecret = (process.env.PHOTON_PROJECT_SECRET || process.env.PHOTON_API_KEY || "").trim();
const E164 = /^\+[1-9]\d{7,14}$/;
const masked = (p) => `…${p.slice(-4)}`;

let appPromise = null;   // one Spectrum connection, started on the first send; retried after a failure
function spectrum() {
  appPromise ??= Spectrum({ projectId, projectSecret, providers: [imessage.config()] })
    .catch((e) => { appPromise = null; throw e; });
  return appPromise;
}

async function sendText(to, text) {
  const im = imessage(await spectrum());
  const user = await im.user(to);
  const dm = await im.space.create(user);
  await dm.send(text);
  let line = null;
  try { line = imessage(dm).phone ?? null; } catch { /* not every space exposes its line */ }
  return line;
}

function reply(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    return reply(res, 200, { ok: true, configured: Boolean(projectId && projectSecret) });
  }
  if (req.method !== "POST" || req.url !== "/send") return reply(res, 404, { ok: false, error: "not found" });
  if (!projectId || !projectSecret) {
    return reply(res, 503, { ok: false, error: "PHOTON_PROJECT_ID / PHOTON_PROJECT_SECRET not set" });
  }
  let body = "";
  for await (const chunk of req) {
    body += chunk;
    if (body.length > 10_000) return reply(res, 413, { ok: false, error: "too large" });
  }
  let to, text;
  try { ({ to, text } = JSON.parse(body)); } catch { return reply(res, 400, { ok: false, error: "bad JSON" }); }
  if (typeof to !== "string" || !E164.test(to)) return reply(res, 422, { ok: false, error: "to must be E.164" });
  if (typeof text !== "string" || !text.trim()) return reply(res, 422, { ok: false, error: "text is empty" });
  try {
    const line = await sendText(to, text);
    console.log(`iMessage sent to ${masked(to)}${line ? ` from ${line}` : ""}`);
    return reply(res, 200, { ok: true, line });
  } catch (e) {
    const error = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
    console.warn(`iMessage to ${masked(to)} failed: ${error}`);
    return reply(res, 502, { ok: false, error });
  }
});

server.listen(PORT, HOST, () => console.log(`messenger on ${HOST}:${PORT} (Photon ${projectId ? "configured" : "not configured"})`));
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, async () => {
    server.close();
    try { await (await appPromise)?.stop(); } catch { /* shutting down anyway */ }
    process.exit(0);
  });
}
