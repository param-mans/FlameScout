// End-to-end test of the live incident overlay: WebSocket -> web app -> viewer iframe. Dev only.
//   cd apps/web && npm i --no-save playwright && npx playwright install webkit
//   node scripts/overlay-e2e.mjs [--python ~/.venvs/flamescout/bin/python] [--shots /tmp/overlay-e2e]
// It starts, by itself and offline:
//   - scripts/mock_incident_ws.py (Fairdale S4 v1 at 1 s and v2 at 6 s; again with --invalid and with --unknown)
//   - a Vite dev server through Vite's JS API with the repo's React/Tailwind setup, /ws proxied to the mock, HMR and
//     Vite's own WebSocket off (so any WebSocket the page opens is ours), and the house-data routes WITHOUT credentials: plans come from the
//     stored raw responses in data/raw (local, not in git); Vexcel is a labelled test stub (grey 8 x 8 SVG images, no
//     capture date). No external API is called.
// Checks (WebKit, touch, 1180 x 820; Chromium too if installed):
//   1. the chip goes LIVE · v1 then LIVE · v2 and [data-incident-version] follows (1, then 2); the viewer switches to
//      Fairdale ([data-live-sample], start: ?sample=214905148)
//   2. --invalid: REJECTED v3 (keeping v2), [data-incident-version] stays 2
//   3. --unknown: "Unknown building: <model_id>", nothing applied, the house does not change
//   4. without ?incident= no WebSocket is opened and there is no live chip
//   5. no console errors (except the expected "[incident] overlay rejected" in check 2)
//   6. scripts/ipad-webkit.mjs passes against the same offline server
// Screenshots only with --shots, to a local folder outside git. Exit code 1 on any failure.
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { createHouseDataHandler } from "../server/handler.mjs";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.resolve(WEB, "..", "..");
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const VENV_PY = path.join(os.homedir(), ".venvs", "flamescout", "bin", "python");
const PYTHON = arg("--python", fs.existsSync(VENV_PY) ? VENV_PY : "python3");
const SHOTS = arg("--shots", null);
const START = "214905148", FAIRDALE = "209217448";
let failures = 0;
const fail = (m) => { failures++; console.log("  FAIL", m); };
const ok = (m) => console.log("  ok", m);

if (!fs.existsSync(path.join(ROOT, "data", "raw", FAIRDALE, "matterport", "raw_floors.json"))) {
  console.log("SKIP: data/raw (local only) is missing; the offline server needs the stored plans"); process.exit(0);
}
let playwright;
try { playwright = await import("playwright"); } catch { console.log("SKIP: Playwright is not installed (npm i --no-save playwright)"); process.exit(0); }

const freePort = () => new Promise((res) => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
const until = async (fn, ms, what) => { const t = Date.now(); for (;;) { if (await fn()) return true; if (Date.now() - t > ms) { fail(`timeout: ${what}`); return false; } await new Promise((r) => setTimeout(r, 150)); } };

// ---- mock WebSocket server (restarted per mode on the same port)
const MOCK_PORT = await freePort();
let mock = null;
async function startMock(flags = []) {
  if (mock) { mock.kill(); await new Promise((r) => mock.once("exit", r)); }
  mock = spawn(PYTHON, [path.join(ROOT, "scripts", "mock_incident_ws.py"), "--port", String(MOCK_PORT), ...flags], { stdio: ["ignore", "inherit", "inherit"] });
  await until(() => new Promise((r) => { const c = net.connect(MOCK_PORT, "127.0.0.1", () => { c.end(); r(true); }); c.on("error", () => r(false)); }), 15000, "mock server");
}

// ---- offline Vite
const STUB_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#3c4048"/></svg>'; // grey test stub, not imagery
const houseData = createHouseDataHandler({
  getEnv: () => undefined, // no credentials: stored plans only, never the live APIs
  catalogFile: path.join(WEB, "public", "house-viewer", "samples", "index.json"),
  storedDir: path.join(ROOT, "data", "raw"),
});
const VEXCEL_STUB = /^\/api\/house-viewer\/vexcel\/[A-Za-z0-9-]+(?:\/((?:oblique-(?:north|east|south|west)|aerial)\.jpg))?$/;
const offlineHouseData = {
  name: "offline-house-data",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const m = VEXCEL_STUB.exec((req.url ?? "").split("?")[0]);
      if (!m) return houseData(req, res, next);
      if (m[1]) { res.setHeader("Content-Type", "image/svg+xml"); return res.end(STUB_IMAGE); }
      const views = Object.fromEntries(["aerial", "north", "east", "south", "west"].map((d) => [d, { file: d === "aerial" ? "aerial.jpg" : `oblique-${d}.jpg`, captureDate: null }]));
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ source: "offline test stub (overlay-e2e)", copyright: "test stub, not imagery", convention: "oblique-<dir> = direction the camera faces", views }));
    });
  },
};
const VITE_PORT = await freePort();
const vite = await createServer({
  configFile: false, root: WEB, logLevel: "warn",
  plugins: [react(), tailwindcss(), offlineHouseData],
  server: { port: VITE_PORT, strictPort: true, hmr: false, ws: false, proxy: { "/ws": { target: `ws://127.0.0.1:${MOCK_PORT}`, ws: true } } },
});
await vite.listen();
const BASE = `http://localhost:${VITE_PORT}`;
console.log(`offline Vite on ${BASE}, mock WebSocket on :${MOCK_PORT}`);

// ---- browser runs
const chip = (page) => page.locator("[data-live-status]");
const version = (page) => page.locator("[data-incident-version]").first().getAttribute("data-incident-version").catch(() => null);
async function open(browserType, query) {
  const browser = await browserType.launch();
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, hasTouch: true, isMobile: browserType.name() === "webkit", deviceScaleFactor: 2 });
  const page = await ctx.newPage(), errors = [], sockets = [], openSockets = new Set();
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("websocket", (ws) => { sockets.push(ws.url()); openSockets.add(ws); ws.on("close", () => openSockets.delete(ws)); });
  await page.goto(`${BASE}/${query}`, { waitUntil: "load" });
  return { browser, page, errors, sockets, openSockets };
}
const text = async (page) => (await chip(page).count()) ? (await chip(page).innerText()).trim() : "";
const liveSample = (page) => page.locator("[data-live-sample]").first().getAttribute("data-live-sample").catch(() => null);
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name) }); } };
const cleanConsole = (errors, allowed = []) => {
  const bad = errors.filter((e) => !allowed.some((a) => e.includes(a)));
  bad.forEach((e) => fail(`console: ${e.slice(0, 160)}`));
  if (!bad.length) ok(`console clean${errors.length ? ` (${errors.length} expected message(s))` : ""}`);
};

const browsers = [playwright.webkit];
try { if (fs.existsSync(playwright.chromium.executablePath())) browsers.push(playwright.chromium); } catch { /* not installed */ }
const LIVE = `?viewer=1&incident=demo&sample=${START}`;

try {
  for (const bt of browsers) {
    console.log(`== ${bt.name()}`);
    // 1. v1 then v2, house switch
    await startMock();
    let r = await open(bt, LIVE);
    if (await until(async () => (await text(r.page)) === "LIVE · v1" && (await version(r.page)) === "1", 30000, "LIVE · v1 and data-incident-version=1")) ok("LIVE · v1, data-incident-version=1");
    if ((await liveSample(r.page)) === FAIRDALE) ok("viewer switched to Fairdale"); else fail(`viewer did not switch to Fairdale: ${await liveSample(r.page)}`);
    await shot(r.page, `${bt.name()}-live-v1.png`);
    if (await until(async () => (await text(r.page)) === "LIVE · v2" && (await version(r.page)) === "2", 20000, "LIVE · v2 and data-incident-version=2")) ok("LIVE · v2, data-incident-version=2");
    const chipBox = await chip(r.page).boundingBox();
    if (chipBox && chipBox.height >= 44) ok(`chip ${Math.round(chipBox.height)} px tall`); else fail(`chip under 44 px: ${chipBox?.height}`);
    // React StrictMode (dev) mounts effects twice: the first socket is opened and closed again; one stays open
    if (r.sockets.length && r.sockets.every((u) => u.endsWith("/ws/incidents/demo")) && r.openSockets.size === 1) ok(`WebSockets only to /ws/incidents/demo, one open (${r.sockets.length} opened)`);
    else fail(`WebSockets: ${JSON.stringify(r.sockets)}, open: ${r.openSockets.size}`);
    await shot(r.page, `${bt.name()}-live-v2.png`);
    cleanConsole(r.errors);
    await r.browser.close();

    // 2. invalid v3
    await startMock(["--invalid"]);
    r = await open(bt, LIVE);
    if (await until(async () => (await text(r.page)) === "REJECTED v3 (keeping v2)", 30000, "REJECTED v3 (keeping v2)")) ok("REJECTED v3 (keeping v2)");
    if ((await version(r.page)) === "2") ok("data-incident-version stays 2"); else fail(`data-incident-version is ${await version(r.page)} after the rejected v3`);
    await shot(r.page, `${bt.name()}-rejected-v3.png`);
    cleanConsole(r.errors, ["[incident] overlay rejected"]);
    await r.browser.close();

    // 3. unknown building
    await startMock(["--unknown"]);
    r = await open(bt, LIVE);
    if (await until(async () => (await text(r.page)) === "Unknown building: unknownModel0", 20000, "Unknown building chip")) ok("Unknown building: unknownModel0");
    await r.page.waitForTimeout(6500); // past v2
    if ((await version(r.page)) === null) ok("nothing applied"); else fail(`data-incident-version is ${await version(r.page)}`);
    if ((await liveSample(r.page)) === START) ok("house unchanged"); else fail(`house changed for an unknown building: ${await liveSample(r.page)}`);
    cleanConsole(r.errors);
    await r.browser.close();

    // 4. no ?incident=: no WebSocket, no chip
    await startMock();
    for (const q of [`?viewer=1&sample=${FAIRDALE}`, `?sample=${START}`]) {
      r = await open(bt, q);
      await r.page.waitForTimeout(4000);
      if (r.sockets.length === 0) ok(`no WebSocket without ?incident= (${q})`); else fail(`WebSocket opened without ?incident=: ${JSON.stringify(r.sockets)}`);
      if ((await chip(r.page).count()) === 0 && (await version(r.page)) === null) ok("no live chip, no data-incident-version"); else fail("live UI shown without ?incident=");
      if (q) await shot(r.page, `${bt.name()}-default.png`);
      cleanConsole(r.errors);
      await r.browser.close();
    }
  }

  // 6. the existing iPad walk-through against the same offline server
  console.log("== ipad-webkit.mjs (offline server)");
  const code = await new Promise((res) => spawn(process.execPath, [path.join(WEB, "scripts", "ipad-webkit.mjs"), "--base", BASE], { stdio: "inherit" }).on("exit", res));
  if (code === 0) ok("ipad-webkit.mjs passed"); else fail(`ipad-webkit.mjs exited with ${code}`);
} finally {
  if (mock) mock.kill();
  await vite.close();
}
console.log(failures ? `${failures} FAILED` : `OK overlay end-to-end (${browsers.map((b) => b.name()).join(", ")})`);
process.exit(failures ? 1 : 0);
