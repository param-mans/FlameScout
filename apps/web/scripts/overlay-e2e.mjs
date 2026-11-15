// End-to-end test of the live incident overlay: API/WebSocket mock -> web app -> viewer iframe. Dev only.
//   cd apps/web && npm i --no-save playwright && npx playwright install webkit
//   node scripts/overlay-e2e.mjs [--python ~/.venvs/flamescout/bin/python] [--shots /tmp/overlay-e2e]
// It starts, by itself and offline:
//   - scripts/mock_incident_ws.py: minimal incident API + WebSocket (Fairdale S4 v1 at 1 s and v2 at 6 s; also with
//     --invalid, --unknown, --off-catalog)
//   - a Vite dev server through Vite's JS API with the repo's React/Tailwind setup, /api/incidents, /api/calls and /ws
//     proxied to the mock, HMR and
//     Vite's own WebSocket off (so any WebSocket the page opens is ours), and the house-data routes WITHOUT credentials: plans come from the
//     stored raw responses in data/raw (local, not in git); Vexcel is a labelled test stub (grey 8 x 8 SVG images, no
//     capture date). No external API is called.
// Checks (WebKit, touch, 1180 x 820; Chromium too if installed):
//   App flow (no URL param, Run call creates the incident):
//   a. Run call -> POST /api/incidents, id in localStorage; the confirmed house (Fairdale) shows before any overlay
//   b. overlays arrive: LIVE · v1 then LIVE · v2
//   c. New call -> second incident: the socket moves to the new id and its overlays show (v1, v2 again)
//   d. --invalid: REJECTED v3 (keeping v2), [data-incident-version] stays 2
//   e. --off-catalog: "No interior scan on file"
//   f. ?mock=tower: no incident WebSocket, no chip (/ws/calls, the Call in feed, is not counted)
//   g. Clear call clears the screen; a Call in from another device -> the viewer follows its incident
//   Explicit ?incident=demo:
//   1. the chip goes LIVE · v1 then LIVE · v2 and [data-incident-version] follows (1, then 2); the viewer switches to
//      Fairdale ([data-live-sample], start: ?sample=214905148)
//   3. --unknown: "Unknown building: <model_id>", nothing applied, the house does not change
//   4. without ?incident= no WebSocket is opened and there is no live chip
//   5. no console errors (except the expected "[incident] overlay rejected" in d, and the 404s of briefs/latest and
//      overlay/latest that the real API also returns before the first brief)
//   6. scripts/ipad-webkit.mjs against the same offline server (mock stopped, app in mock mode): prints its FAIL count
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
  server: { port: VITE_PORT, strictPort: true, hmr: false, ws: false, proxy: {
    "/ws": { target: `ws://127.0.0.1:${MOCK_PORT}`, ws: true },
    "^/api/(incidents|calls)": { target: `http://127.0.0.1:${MOCK_PORT}` },   // the house-data routes stay local
  } },
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
  // only the incident sockets count: /ws/calls (Call in feed, origin/main) opens on every screen, ?mock=tower included
  page.on("websocket", (ws) => { if (/\/ws\/calls$/.test(ws.url())) return; sockets.push(ws.url()); openSockets.add(ws); ws.on("close", () => openSockets.delete(ws)); });
  await page.goto(`${BASE}/${query}`, { waitUntil: "load" });
  return { browser, page, errors, sockets, openSockets };
}
const text = async (page) => (await chip(page).count()) ? (await chip(page).innerText()).trim() : "";
const liveSample = (page) => page.locator("[data-live-sample]").first().getAttribute("data-live-sample").catch(() => null);
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name) }); } };
const NOT_FOUND = "404"; // briefs/latest + overlay/latest before the first brief (the real API answers 404 too)
const cleanConsole = (errors, allowed = []) => {
  // /ws/calls is the Call in feed (live/callFeed.ts, origin/main): StrictMode closes its first socket while connecting,
  // the same messages show on origin/main; only this test's own sockets (/ws/incidents) are judged here.
  const bad = errors.filter((e) => ![...allowed, NOT_FOUND, "/ws/calls"].some((a) => e.includes(a)));
  bad.forEach((e) => fail(`console: ${e.slice(0, 160)}`));
  if (!bad.length) ok(`console clean${errors.length ? ` (${errors.length} expected message(s))` : ""}`);
};

const browsers = [playwright.webkit];
try { if (fs.existsSync(playwright.chromium.executablePath())) browsers.push(playwright.chromium); } catch { /* not installed */ }
const LIVE = `?viewer=1&incident=demo&sample=${START}`;

const storedId = (page) => page.evaluate(() => localStorage.getItem("flamescout.incident")).catch(() => null);
async function runCall(page, label = "Run call") {
  const btn = page.getByRole("button", { name: label, exact: true });
  if (!(await until(async () => (await btn.count()) > 0, 20000, `"${label}" button`))) return null;
  const before = await storedId(page);
  await btn.click();
  let id = null;
  await until(async () => { id = await storedId(page); return !!id && id !== before; }, 10000, "new incident id in localStorage");
  return id;
}
const waitChip = (page, want, ms = 30000) => until(async () => (await text(page)) === want, ms, want);

try {
  for (const bt of browsers) {
    console.log(`== ${bt.name()} app flow`);
    // a-c. Run call, confirmed house before any overlay, overlays, second incident
    await startMock(["--v1", "20", "--v2", "24"]);
    let r = await open(bt, `?sample=${START}`);
    const id1 = await runCall(r.page);
    if (id1) ok(`a. incident created: ${id1}`);
    if (await until(async () => (await r.page.locator(`[data-confirmed-building="${FAIRDALE}"]`).count()) > 0 && (await liveSample(r.page)) === FAIRDALE, 15000, "confirmed Fairdale plan")) {
      const v = await version(r.page), t = await text(r.page);
      if (v === null && !/· v\d/.test(t)) ok(`a. Fairdale plan shown, no overlay yet (chip: ${t})`); else fail(`a. overlay already applied: v=${v} chip=${t}`);
    }
    await shot(r.page, `${bt.name()}-app-confirmed.png`);
    if (await waitChip(r.page, "LIVE · v1")) ok("b. LIVE · v1");
    if (await waitChip(r.page, "LIVE · v2", 15000) && (await version(r.page)) === "2") ok("b. LIVE · v2, data-incident-version=2");
    const id2 = await runCall(r.page, "New call");
    if (id2 && id2 !== id1) ok(`c. second incident: ${id2}`); else fail(`c. no second incident (${id2})`);
    if (await until(async () => /waiting for overlay|connecting/.test(await text(r.page)) || (await text(r.page)) === "LIVE · v1", 10000, "chip reset for the new incident")) ok(`c. chip reset (${await text(r.page)})`);
    if (await waitChip(r.page, "LIVE · v1") && await waitChip(r.page, "LIVE · v2", 15000)) ok("c. new incident's v1 then v2");
    const open2 = [...r.openSockets].map((w) => w.url());
    if (open2.length === 1 && open2[0].endsWith(`/ws/incidents/${id2}`)) ok("c. one socket open, on the new incident"); else fail(`c. open sockets: ${JSON.stringify(open2)}`);
    // g. Clear call, then a Call in from another device: the screen clears and the viewer follows the new incident
    await r.page.getByRole("button", { name: /Clear call/ }).first().tap();
    if (await until(async () => (await version(r.page)) === null && (await r.page.evaluate(() => localStorage.getItem("flamescout.incident"))) === null, 10000, "cleared")) ok("g. Clear call: overlay and incident cleared");
    const id3 = (await (await r.page.request.post(`${BASE}/api/calls/web/callin`)).json()).incident_id;
    if (await until(async () => (await r.page.evaluate(() => localStorage.getItem("flamescout.incident"))) === id3, 10000, "attached to the Call in")) ok(`g. app follows the Call in: ${id3}`);
    await r.page.getByRole("button", { name: /^MDT$/ }).first().tap();
    if (await waitChip(r.page, "LIVE · v1") && await waitChip(r.page, "LIVE · v2", 15000)) ok("g. viewer shows the new incident's v1 then v2");
    const open3 = [...r.openSockets].map((w) => w.url());
    if (open3.length === 1 && open3[0].endsWith(`/ws/incidents/${id3}`)) ok("g. one incident socket, on the Call in's incident"); else fail(`g. open sockets: ${JSON.stringify(open3)}`);
    cleanConsole(r.errors);
    await r.browser.close();

    // d. invalid v3 (app flow)
    await startMock(["--invalid"]);
    r = await open(bt, `?sample=${START}`);
    await runCall(r.page);
    if (await waitChip(r.page, "REJECTED v3 (keeping v2)")) ok("d. REJECTED v3 (keeping v2)");
    if ((await version(r.page)) === "2") ok("d. data-incident-version stays 2"); else fail(`d. data-incident-version is ${await version(r.page)} after the rejected v3`);
    await shot(r.page, `${bt.name()}-rejected-v3.png`);
    cleanConsole(r.errors, ["[incident] overlay rejected"]);
    await r.browser.close();

    // e. off-catalog building
    await startMock(["--off-catalog"]);
    r = await open(bt, `?sample=${START}`);
    await runCall(r.page);
    if (await until(async () => (await r.page.locator("[data-no-scan]").count()) > 0, 15000, "No interior scan on file")
      && /NO INTERIOR SCAN ON FILE/i.test(await r.page.locator("[data-no-scan]").innerText())) ok("e. No interior scan on file");
    await shot(r.page, `${bt.name()}-off-catalog.png`);
    cleanConsole(r.errors);
    await r.browser.close();

    // f. tower mock: no socket, no chip
    await startMock();
    r = await open(bt, "?mock=tower");
    await r.page.waitForTimeout(3000);
    if (r.sockets.length === 0 && (await chip(r.page).count()) === 0) ok("f. ?mock=tower: no WebSocket, no chip"); else fail(`f. ?mock=tower: sockets ${JSON.stringify(r.sockets)}, chip ${await chip(r.page).count()}`);
    cleanConsole(r.errors);
    await r.browser.close();

    console.log(`== ${bt.name()} explicit ?incident=`);
    // 1. v1 then v2, house switch
    await startMock();
    r = await open(bt, LIVE);
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

    // 3. unknown building
    await startMock(["--unknown"]);
    r = await open(bt, LIVE);
    if (await until(async () => (await text(r.page)) === "Unknown building: unknownModel0", 20000, "Unknown building chip")) ok("Unknown building: unknownModel0");
    await r.page.waitForTimeout(6500); // past v2
    if ((await version(r.page)) === null) ok("nothing applied"); else fail(`data-incident-version is ${await version(r.page)}`);
    if ((await liveSample(r.page)) === FAIRDALE) ok("house = confirmed building, not the overlay"); else fail(`house changed for an unknown building: ${await liveSample(r.page)}`);
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
  // Mock stopped first: the app falls back to mock mode, as ipad-webkit.mjs expects. It already fails on origin/main
  // (small touch targets), so only its FAIL count is reported here; compare it with origin/main.
  // A second offline Vite without the /api proxy, as on origin/main (the app falls back to mock mode).
  if (mock) { mock.kill(); await new Promise((r) => mock.once("exit", r)); mock = null; }
  const IPAD_PORT = await freePort();
  const vite2 = await createServer({ configFile: false, root: WEB, logLevel: "warn", plugins: [react(), tailwindcss(), offlineHouseData],
    server: { port: IPAD_PORT, strictPort: true, hmr: false, ws: false, proxy: { "/ws": { target: `ws://127.0.0.1:${MOCK_PORT}`, ws: true } } } });
  await vite2.listen();
  console.log("== ipad-webkit.mjs (offline server, informational)");
  let out = "";
  const code = await new Promise((res) => { const c = spawn(process.execPath, [path.join(WEB, "scripts", "ipad-webkit.mjs"), "--base", `http://localhost:${IPAD_PORT}`], { stdio: ["ignore", "pipe", "inherit"] }); c.stdout.on("data", (d) => { out += d; process.stdout.write(d); }); c.on("exit", res); });
  console.log(`  ipad-webkit.mjs exit ${code}, FAIL lines: ${out.split("\n").filter((l) => /FAIL/.test(l)).length}`);
  await vite2.close();
} finally {
  if (mock) mock.kill();
  await vite.close();
}
console.log(failures ? `${failures} FAILED` : `OK overlay end-to-end (${browsers.map((b) => b.name()).join(", ")})`);
process.exit(failures ? 1 : 0);
