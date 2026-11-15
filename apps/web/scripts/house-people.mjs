// House screen people, fire glow and routes (viewer layout=house + src/house/HouseMdt.tsx PEOPLE card): WebKit, iPad profile
// (1180 x 820, touch, mobile, 2x), offline. Dev only.
//   cd apps/web && npm i --no-save playwright && npx playwright install webkit
//   node scripts/house-people.mjs [--python ~/.venvs/flamescout/bin/python] [--port 5182]
// Starts scripts/mock_incident_ws.py (Fairdale confirmed, overlays far in the future so none arrives) on --port + 1 and an
// offline Vite on --port (stored plans from data/raw, Vexcel test stub). Then posts the example overlays of
// public/house-viewer/incident/examples (fairdale_people_fire / _two_bathrooms / _floor_level) to the viewer and checks:
//   - before any overlay: PEOPLE card shows a dash, no figures or routes;
//   - green and yellow figure counts (hidden data-people-green / -yellow and the drawn figures in 2D and 3D);
//   - one red and one green route (data-route-red / -green, drawn in 2D and 3D), data-unreachable = 0;
//   - the fire glow does not leak outside the house outline (2D screenshot with and without the glow, compared pixel by
//     pixel outside the room polygons; screenshots stay in a temp folder) and is visible inside;
//   - the PEOPLE card total and rooms line; a tapped room's exit route is white; no text in the viewer; clean console.
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
const VITE_PORT = Number(arg("--port", 5182)), MOCK_PORT = VITE_PORT + 1;
const START = "214905148", FAIRDALE = "209217448", ROOM = "d3slsxy3zlg";
const EX = path.join(WEB, "public", "house-viewer", "incident", "examples");
const CASES = [
  { file: "fairdale_people_fire.json", green: 3, yellow: 2, total: "4", rooms: "Office 1 · Bedroom 2 · Bathroom ? 1", leak: true },
  { file: "fairdale_two_bathrooms.json", green: 0, yellow: 2, total: "1", rooms: "Bathroom ? 1" },
  { file: "fairdale_floor_level.json", green: 0, yellow: 1, total: "1", rooms: "Floor 2 ? 1" },
];
const SHOTS = fs.mkdtempSync(path.join(os.tmpdir(), "house-people-"));
let failures = 0;
const fail = (m) => { failures++; console.log("  FAIL", m); };
const ok = (m) => console.log("  ok", m);
const check = (cond, m, detail = "") => (cond ? ok(m) : fail(`${m} ${detail}`));

if (!fs.existsSync(path.join(ROOT, "data", "raw", FAIRDALE, "matterport", "raw_floors.json"))) { console.log("SKIP: data/raw (local only) is missing"); process.exit(0); }
let playwright;
try { playwright = await import("playwright"); } catch { console.log("SKIP: Playwright is not installed (npm i --no-save playwright)"); process.exit(0); }

const until = async (fn, ms, what) => { const t = Date.now(); for (;;) { if (await fn()) return true; if (Date.now() - t > ms) { fail(`timeout: ${what}`); return false; } await new Promise((r) => setTimeout(r, 150)); } };
const mock = spawn(PYTHON, [path.join(ROOT, "scripts", "mock_incident_ws.py"), "--port", String(MOCK_PORT), "--v1", "100000", "--v2", "100000"], { stdio: ["ignore", "ignore", "inherit"] });
await until(() => new Promise((r) => { const c = net.connect(MOCK_PORT, "127.0.0.1", () => { c.end(); r(true); }); c.on("error", () => r(false)); }), 15000, "mock server");

const STUB_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#3c4048"/></svg>';
const houseData = createHouseDataHandler({ getEnv: () => undefined, catalogFile: path.join(WEB, "public", "house-viewer", "samples", "index.json"), storedDir: path.join(ROOT, "data", "raw") });
const VEXCEL_STUB = /^\/api\/house-viewer\/vexcel\/[A-Za-z0-9-]+(?:\/((?:oblique-(?:north|east|south|west)|aerial)\.jpg))?$/;
const offlineHouseData = { name: "offline-house-data", configureServer(server) {
  server.middlewares.use((req, res, next) => {
    const m = VEXCEL_STUB.exec((req.url ?? "").split("?")[0]);
    if (!m) return houseData(req, res, next);
    if (m[1]) { res.setHeader("Content-Type", "image/svg+xml"); return res.end(STUB_IMAGE); }
    const views = Object.fromEntries(["aerial", "north", "east", "south", "west"].map((d) => [d, { file: d === "aerial" ? "aerial.jpg" : `oblique-${d}.jpg`, captureDate: null }]));
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ source: "offline test stub (house-people)", copyright: "test stub, not imagery", views }));
  });
} };
const vite = await createServer({ configFile: false, root: WEB, logLevel: "warn", plugins: [react(), tailwindcss(), offlineHouseData],
  server: { port: VITE_PORT, strictPort: true, hmr: false, ws: false, proxy: { "/ws": { target: `ws://127.0.0.1:${MOCK_PORT}`, ws: true }, "^/api/(incidents|calls)": { target: `http://127.0.0.1:${MOCK_PORT}` } } } });
await vite.listen();
const BASE = `http://localhost:${VITE_PORT}`;

// pixels of two screenshots of the 2D plan, compared outside / inside the floor's room polygons (CSS px, element-relative)
async function compare(page, pngA, pngB, polys, dsf) {
  return page.evaluate(async ([a, b, polys, dsf]) => {
    const load = (b64) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = "data:image/png;base64," + b64; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    const px = (img) => { const c = document.createElement("canvas"); c.width = img.width; c.height = img.height; const g = c.getContext("2d"); g.drawImage(img, 0, 0); return g.getImageData(0, 0, c.width, c.height).data; };
    const A = px(ia), B = px(ib), W = ia.width, H = ia.height;
    const inside = (p, ring) => { let d = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const [xi, yi] = ring[i], [xj, yj] = ring[j]; if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) d = !d; } return d; };
    const segD = (p, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy, t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0; return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy); };
    const nearEdge = (p) => polys.some((r) => r.some((q, i) => segD(p, q, r[(i + 1) % r.length]) < 3));
    let outDiff = 0, outRed = 0, inDiff = 0, outN = 0;
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) {
      const p = [x / dsf, y / dsf], k = (y * W + x) * 4;
      const diff = Math.abs(A[k] - B[k]) + Math.abs(A[k + 1] - B[k + 1]) + Math.abs(A[k + 2] - B[k + 2]);
      if (polys.some((r) => inside(p, r))) { if (diff > 12) inDiff++; continue; }
      if (nearEdge(p)) continue; // antialiasing of the outline itself
      outN++;
      if (diff > 12) outDiff++;
      if (B[k] > 90 && B[k] > B[k + 1] + 50 && B[k] > B[k + 2] + 50) outRed++;
    }
    return { outDiff, outRed, inDiff, outN };
  }, [pngA.toString("base64"), pngB.toString("base64"), polys, dsf]);
}

try {
  const browser = await playwright.webkit.launch();
  const DSF = 2;
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, hasTouch: true, isMobile: true, deviceScaleFactor: DSF });
  const page = await ctx.newPage(), errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  console.log("== webkit 1180x820 (iPad profile)");
  await page.goto(`${BASE}/?sample=${START}`, { waitUntil: "load" });
  const run = page.getByRole("button", { name: "Run call", exact: true });
  await until(async () => (await run.count()) > 0, 20000, "Run call button");
  await run.click();
  await until(async () => (await page.locator(`[data-house-mdt][data-confirmed-building="${FAIRDALE}"]`).count()) > 0, 15000, "house screen");
  const vf = () => page.frames().find((f) => f.url().includes("casa.html"));
  await until(async () => !!vf() && (await vf().evaluate(() => !!(window.__casa && window.__casa.INC.index && document.querySelector("#vista2d svg")))), 20000, "viewer plan loaded");
  await page.waitForTimeout(800);
  const f = vf();
  const attrs = () => f.evaluate(() => ({ ...document.body.dataset }));
  const card = async () => ({ total: (await page.locator("[data-people-total]").innerText()).trim(), rooms: (await page.locator("[data-people-rooms]").innerText()).trim(), label: (await page.locator("[data-people-card] .label").innerText()).trim() });

  // no overlay yet
  let c = await card(), a = await attrs();
  check(c.label.toUpperCase() === "PEOPLE" && c.total === "—" && c.rooms === "", "no overlay: PEOPLE card shows a dash, no text", JSON.stringify(c));
  check(a.peopleGreen === "0" && a.peopleYellow === "0" && a.routeRed === "0" && a.routeGreen === "0", "no overlay: no figures, no routes", JSON.stringify(a));
  check((await page.locator("[data-house-mdt]").innerText()).toUpperCase().indexOf("CLOSEST EXPOSURE") < 0, "CLOSEST EXPOSURE card replaced");

  for (const K of CASES) {
    console.log(`-- ${K.file}`);
    const ov = JSON.parse(fs.readFileSync(path.join(EX, K.file), "utf8"));
    await page.evaluate((ov) => document.querySelector("iframe").contentWindow.postMessage({ type: "setIncident", overlay: ov }, location.origin), ov);
    await until(async () => (await f.evaluate((id) => window.__casa.INC.actual?.incident_id === id, ov.incident_id)), 8000, "overlay applied");
    await until(async () => (await page.locator("[data-people-total]").innerText()).trim() === K.total, 8000, "card total");
    await f.evaluate(() => window.__casa.setFoco("all")); await page.waitForTimeout(400);
    a = await attrs();
    check(a.peopleGreen === String(K.green) && a.peopleYellow === String(K.yellow), `figures: ${a.peopleGreen} green, ${a.peopleYellow} yellow`, `expected ${K.green} / ${K.yellow}`);
    check(a.routeRed === "1" && a.routeGreen === "1" && a.unreachable === "0", "one red and one green route, nothing unreachable", JSON.stringify(a));
    // drawn: 3D meshes and 2D elements over both floors
    const d3 = await f.evaluate(() => { const c = window.__casa, all = c.P.flatMap((F) => F.g.children); return { green: all.filter((o) => o.userData.persona === "green").length, yellow: all.filter((o) => o.userData.persona === "yellow").length, red: all.filter((o) => o.userData.ruta === "#ef4444").length, gr: all.filter((o) => o.userData.ruta === "#22c55e").length, glow: all.filter((o) => o.userData.fuego).length }; });
    check(d3.green === K.green && d3.yellow === K.yellow && d3.red > 0 && d3.gr > 0 && d3.glow > 1, "3D: figures, both routes and the fire glow drawn", JSON.stringify(d3));
    const d2 = { green: 0, yellow: 0, red: 0, gr: 0, glow: 0 };
    for (const fi of [0, 1]) {
      await f.evaluate((fi) => window.__casa.setFoco(fi), fi); await page.waitForTimeout(250);
      const n = await f.evaluate(() => ({ green: document.querySelectorAll('#vista2d [data-person="green"]').length, yellow: document.querySelectorAll('#vista2d [data-person="yellow"]').length,
        red: document.querySelectorAll('#vista2d [data-route="red"] path').length, gr: document.querySelectorAll('#vista2d [data-route="green"] path').length, glow: document.querySelectorAll("#vista2d [data-fire-glow]").length }));
      for (const k of Object.keys(d2)) d2[k] += n[k];
    }
    check(d2.green === K.green && d2.yellow === K.yellow && d2.red > 0 && d2.gr > 0 && d2.glow === 1, "2D: figures, both routes and one fire glow drawn", JSON.stringify(d2));
    c = await card();
    check(c.total === K.total && c.rooms === K.rooms, `PEOPLE card: ${c.total} · "${c.rooms}"`, `expected ${K.total} · "${K.rooms}"`);
    const oneLine = await page.locator("[data-people-rooms]").evaluate((el) => el.getBoundingClientRect().height <= 20);
    check(oneLine, "rooms on one line");
    const txt = await f.evaluate(() => [...document.querySelectorAll("#vista2d text")].map((t) => t.textContent).filter((t) => /\d/.test(t)));
    check(!txt.length && !(await f.evaluate(() => window.__casa.incSprites())), "no numbers or incident text in the viewer", JSON.stringify(txt));

    if (K.leak) {
      // ground floor (the kitchen fire): glow hidden vs shown, people and routes hidden in both
      await f.evaluate(() => window.__casa.setFoco(0)); await page.waitForTimeout(400);
      await f.addStyleTag({ content: "#vista2d [data-route], #vista2d [data-person] { display: none !important }" });
      const el = f.locator("#vista2d");
      const polys = await f.evaluate(() => {
        const svg = document.querySelector("#vista2d svg"), m = svg.getScreenCTM(), box = document.getElementById("vista2d").getBoundingClientRect(), c = window.__casa;
        return c.S.plan.floors[0].rooms.filter((r) => r.type !== "Void" && r.polygon && (r.polygonQuality || "ok") === "ok")
          .map((r) => r.polygon.map(([x, y]) => [m.a * x + m.c * -y + m.e - box.left, m.b * x + m.d * -y + m.f - box.top]));
      });
      const withGlow = await el.screenshot({ path: path.join(SHOTS, "glow.png") });
      await f.addStyleTag({ content: "#vista2d [data-fire-glow] { display: none !important }" });
      const noGlow = await el.screenshot({ path: path.join(SHOTS, "noglow.png") });
      const r = await compare(page, noGlow, withGlow, polys, DSF);
      check(r.outDiff === 0 && r.outRed === 0, `glow stays inside the outline (${r.outN} outside pixels sampled, ${r.outDiff} changed, ${r.outRed} red)`);
      check(r.inDiff > 500, `glow visible inside the house (${r.inDiff} pixels changed)`);
      await f.evaluate(() => document.querySelectorAll("head style").forEach((s) => { if (s.textContent.includes("display: none !important")) s.remove(); }));
      await page.screenshot({ path: path.join(SHOTS, "house-people.png") });
    }
  }

  // tapped room: its fastest exit route, in white
  await f.evaluate(() => window.__casa.setFoco(0)); await page.waitForTimeout(400);
  await f.locator(`#vista2d path[data-id="${ROOM}"]`).tap({ force: true }); await page.waitForTimeout(500);
  const tap = await f.evaluate(() => ({ stroke: document.querySelector('#vista2d polyline[data-route="tap"]')?.getAttribute("stroke"), d3: window.__casa.refs?.rutaG?.children.map((o) => o.material.color.getHexString()) ?? [] }));
  check(tap.stroke === "#ffffff" && tap.d3.length > 0 && tap.d3.every((h) => h === "ffffff"), "tapped room: exit route in white (2D and 3D)", JSON.stringify(tap));
  await f.locator(`#vista2d path[data-id="${ROOM}"]`).tap({ force: true });

  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)), "no horizontal scroll");
  const bad = errors.filter((e) => !["404", "/ws/calls"].some((x) => e.includes(x)));
  check(!bad.length, `console clean${errors.length ? ` (${errors.length} expected)` : ""}`, bad.map((e) => e.slice(0, 160)).join(" | "));
  console.log(`   screenshots: ${SHOTS}`);
  await ctx.close();
  await browser.close();
} finally {
  mock.kill("SIGKILL");
  await vite.close();
}
console.log(failures ? `${failures} FAILED` : "OK house people (webkit, 1180x820)");
process.exit(failures ? 1 : 0);
