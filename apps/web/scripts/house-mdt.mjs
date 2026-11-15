// House MDT screen (src/house/HouseMdt.tsx + viewer layout=house): checks with WebKit and touch, offline. Dev only.
//   cd apps/web && npm i --no-save playwright && npx playwright install webkit
//   node scripts/house-mdt.mjs [--python ~/.venvs/flamescout/bin/python] [--shots <folder outside the repo>] [--measure]
// Starts scripts/mock_incident_ws.py (Fairdale confirmed, S4 v1 at 1 s and v2 at 6 s) and an offline Vite (same setup as
// overlay-e2e.mjs: stored plans from data/raw, Vexcel test stub, no external API). At 1180 x 820 and 1024 x 768:
//   - none of the removed texts is visible on the page or inside the viewer iframe;
//   - the 3D canvas and the 2D plan are larger than on the previous layout (BEFORE, measured with --measure);
//   - plan and Surroundings are visible at the same time; the live chip is hidden, its attributes are still there;
//   - size-up: at most 6 rows (label >= 16 px, value >= 20 px bold);
//   - tapping a room draws its exit route in 2D and 3D, no panel; tapping again or outside clears it;
//   - no text in the viewer except room names on reported rooms; touch targets >= 44 px; no horizontal scroll; clean console.
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
const SHOTS = arg("--shots", null), MEASURE = process.argv.includes("--measure");
const START = "214905148", FAIRDALE = "209217448", ROOM = "d3slsxy3zlg"; // a Fairdale bedroom with a route to an exterior door
// Viewer areas (CSS px²) on the previous house screen (ConfirmedHouseView + layout=split, no brief), measured with
// --measure on commit 328dc88. The MdtBrief screen of that commit (with a brief) had an even smaller center column.
const BEFORE = {
  "1180x820": { d3: 285675, d2: 171000 },
  "1024x768": { d3: 216177, d2: 139800 },
};
const REMOVED = ["Inspector", "LAYERS", "FIT 2D", "FLOOR PLAN", "LOCATION CONFIRMED", "UNKNOWN", "mark wrong", "mark outdated", "COSTAR", "CHG", "LIVE", "Legend", "Entities",
  "SCAN", "STRUCTURE FIRE", "CHANGED SINCE", "Considerations · incident", "estimate", "stale", "min ago", "INCIDENT", "No incident overlay"];
const VIEWER_ONLY = ["reported", "FIRE", "SLEEP", "IGNITION"]; // the "Reported floor" card stays on the page
const INTERACTIVE = "button, a[href], input:not([type=hidden]), select, summary, [role=button]";
let failures = 0;
const fail = (m) => { failures++; console.log("  FAIL", m); };
const ok = (m) => console.log("  ok", m);

if (!fs.existsSync(path.join(ROOT, "data", "raw", FAIRDALE, "matterport", "raw_floors.json"))) { console.log("SKIP: data/raw (local only) is missing"); process.exit(0); }
let playwright;
try { playwright = await import("playwright"); } catch { console.log("SKIP: Playwright is not installed (npm i --no-save playwright)"); process.exit(0); }

const freePort = () => new Promise((res) => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
const until = async (fn, ms, what) => { const t = Date.now(); for (;;) { if (await fn()) return true; if (Date.now() - t > ms) { fail(`timeout: ${what}`); return false; } await new Promise((r) => setTimeout(r, 150)); } };

const MOCK_PORT = await freePort();
const mock = spawn(PYTHON, [path.join(ROOT, "scripts", "mock_incident_ws.py"), "--port", String(MOCK_PORT)], { stdio: ["ignore", "inherit", "inherit"] });
await until(() => new Promise((r) => { const c = net.connect(MOCK_PORT, "127.0.0.1", () => { c.end(); r(true); }); c.on("error", () => r(false)); }), 15000, "mock server");

const STUB_IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#3c4048"/></svg>'; // grey test stub, not imagery
const houseData = createHouseDataHandler({ getEnv: () => undefined, catalogFile: path.join(WEB, "public", "house-viewer", "samples", "index.json"), storedDir: path.join(ROOT, "data", "raw") });
const VEXCEL_STUB = /^\/api\/house-viewer\/vexcel\/[A-Za-z0-9-]+(?:\/((?:oblique-(?:north|east|south|west)|aerial)\.jpg))?$/;
const offlineHouseData = { name: "offline-house-data", configureServer(server) {
  server.middlewares.use((req, res, next) => {
    const m = VEXCEL_STUB.exec((req.url ?? "").split("?")[0]);
    if (!m) return houseData(req, res, next);
    if (m[1]) { res.setHeader("Content-Type", "image/svg+xml"); return res.end(STUB_IMAGE); }
    const views = Object.fromEntries(["aerial", "north", "east", "south", "west"].map((d) => [d, { file: d === "aerial" ? "aerial.jpg" : `oblique-${d}.jpg`, captureDate: d === "north" ? "2024-03-02" : null }]));
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ source: "offline test stub (house-mdt)", copyright: "test stub, not imagery", views }));
  });
} };
const VITE_PORT = await freePort();
const vite = await createServer({ configFile: false, root: WEB, logLevel: "warn", plugins: [react(), tailwindcss(), offlineHouseData],
  server: { port: VITE_PORT, strictPort: true, hmr: false, ws: false, proxy: { "/ws": { target: `ws://127.0.0.1:${MOCK_PORT}`, ws: true }, "^/api/(incidents|calls)": { target: `http://127.0.0.1:${MOCK_PORT}` } } } });
await vite.listen();
const BASE = `http://localhost:${VITE_PORT}`;

async function tooSmall(frame, scope = "body") {
  return frame.evaluate(([sel, scope]) => {
    const out = [];
    for (const el of document.querySelectorAll(scope + " :is(" + sel + ")")) {
      const t = el.matches("input") && el.closest("label") ? el.closest("label") : el, r = t.getBoundingClientRect(), cs = getComputedStyle(t);
      if (!r.width || !r.height || cs.visibility === "hidden" || cs.display === "none" || t.closest("[hidden]")) continue;
      if (el.closest("details:not([open])") && !el.matches("summary")) continue;
      if (r.width < 44 || r.height < 44) out.push(`<${el.tagName.toLowerCase()}> "${(el.textContent || "").trim().slice(0, 20)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return out;
  }, [INTERACTIVE, scope]);
}
const area = (b) => (b ? Math.round(b.width * b.height) : 0);
const visibleBox = (frame, sel) => frame.evaluate((s) => {
  const el = document.querySelector(s); if (!el) return null;
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  return r.width && r.height && cs.display !== "none" && cs.visibility !== "hidden" && !el.closest("[hidden]") ? { x: r.x, y: r.y, width: r.width, height: r.height } : null;
}, sel);

const measured = {};
try {
  const browser = await playwright.webkit.launch();
  for (const [w, h] of [[1180, 820], [1024, 768]]) {
    const key = `${w}x${h}`;
    console.log(`== webkit ${key}`);
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
    const page = await ctx.newPage(), errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`${BASE}/?sample=${START}`, { waitUntil: "load" });
    const run = page.getByRole("button", { name: "Run call", exact: true });
    await until(async () => (await run.count()) > 0, 20000, "Run call button");
    await run.click();
    const vf = () => page.frames().find((f) => f.url().includes("casa.html"));
    await until(async () => (await page.locator(`[data-confirmed-building="${FAIRDALE}"]`).count()) > 0, 15000, "house screen");
    await until(async () => (await page.locator("[data-incident-version]").first().getAttribute("data-incident-version", { timeout: 300 }).catch(() => null)) === "2", 30000, "overlay v2 applied");
    await until(async () => !!vf() && !!(await visibleBox(vf(), "#vista2d svg")), 15000, "2D plan");
    await page.waitForTimeout(1500);
    const f = vf(), frameBox = await page.locator("iframe").boundingBox();
    const d3 = area(await visibleBox(f, "#vista3d canvas")), d2 = area(await visibleBox(f, "#vista2d"));
    measured[key] = { d3, d2 };
    console.log(`   3D ${d3} px², 2D ${d2} px² (iframe ${Math.round(frameBox?.width ?? 0)}x${Math.round(frameBox?.height ?? 0)})`);
    if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `house-mdt-${key}${MEASURE ? "-before" : ""}.png`) }); }
    if (MEASURE) { await ctx.close(); continue; }

    // areas
    // BEFORE was measured under a one-row top bar (36 px); scale it by the height the current bar leaves
    const nav = Math.round((await page.locator("nav").first().boundingBox())?.height ?? 36), k = (h - nav) / (h - 36);
    const b = { d3: Math.round(BEFORE[key].d3 * k), d2: Math.round(BEFORE[key].d2 * k) };
    console.log(`   top bar ${nav} px (baseline x${k.toFixed(3)})`);
    if (d3 > b.d3) ok(`3D larger: ${d3} > ${b.d3} px² (+${Math.round((d3 / b.d3 - 1) * 100)} %)`); else fail(`3D not larger: ${d3} <= ${b.d3}`);
    if (d2 > b.d2) ok(`2D larger: ${d2} > ${b.d2} px² (+${Math.round((d2 / b.d2 - 1) * 100)} %)`); else fail(`2D not larger: ${d2} <= ${b.d2}`);
    const img = await visibleBox(f, "#oblicuas-body .obl-img"), plan = await visibleBox(f, "#vista2d");
    if (img && plan && img.height >= 100 && img.y >= plan.y + plan.height - 1) ok("plan and Surroundings visible together (Surroundings below)"); else fail(`plan / Surroundings not both visible: ${JSON.stringify({ img, plan })}`);
    if ((await f.locator("#oblicuas-body [data-obl]").count()) === 4) ok("Surroundings: N / E / S / W selector"); else fail(`Surroundings selector: ${await f.locator("#oblicuas-body [data-obl]").count()} buttons`);

    // removed texts (visible text of the page and of the iframe)
    // page: the house screen (the app navigation above it, with its own LIVE mode indicator, is not part of it)
    const pageText = await page.locator("[data-house-mdt]").innerText(), viewerText = await f.evaluate(() => document.body.innerText);
    for (const [where, t] of [["page", pageText], ["viewer", viewerText]]) {
      const hits = (where === "viewer" ? [...REMOVED, ...VIEWER_ONLY] : REMOVED).filter((s) => new RegExp(`(^|[^A-Za-z])${s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Za-z]|$)`, "i").test(t));
      if (hits.length) fail(`${where} shows removed text: ${hits.join(", ")}`); else ok(`${where}: none of the removed texts`);
    }
    // hidden live status: attributes kept, chip not visible
    const chipVisible = await page.locator("[data-live-status]").first().isVisible().catch(() => false);
    const statusText = await page.locator("[data-live-status-text]").first().getAttribute("data-live-status-text", { timeout: 300 }).catch(() => null);
    const liveSample = await page.locator("[data-live-sample]").first().getAttribute("data-live-sample", { timeout: 300 }).catch(() => null);
    if (!chipVisible && statusText === "LIVE · v2" && liveSample === FAIRDALE) ok(`live chip hidden; data-live-status-text="${statusText}", data-live-sample=${liveSample}`);
    else fail(`live status: visible=${chipVisible} text=${statusText} sample=${liveSample}`);
    const attrsVisible = await page.locator("[data-incident-version]").first().evaluate((el) => el.innerText.includes("v2"));
    if (!attrsVisible) ok("no version text on screen"); else fail("version text visible");

    // size-up
    const rows = await page.locator("[data-sizeup-row]").evaluateAll((els) => els.map((el) => {
      const k = el.querySelector("[data-k]"), v = el.querySelector("[data-v]"), ck = getComputedStyle(k), cv = getComputedStyle(v);
      return { k: k.textContent, v: v.textContent, ks: parseFloat(ck.fontSize), vs: parseFloat(cv.fontSize), vw: Number(cv.fontWeight) };
    }));
    if (rows.length >= 1 && rows.length <= 6 && rows.every((r) => r.ks >= 16 && r.vs >= 20 && r.vw >= 600 && r.v.trim())) ok(`size-up: ${rows.map((r) => `${r.k}=${r.v}`).join(", ")}`);
    else fail(`size-up rows: ${JSON.stringify(rows)}`);

    // viewer text: no sprites with words, 2D texts only room names of reported rooms
    const info = await f.evaluate(() => {
      const c = window.__casa, names = new Set(c.S.plan.floors.flatMap((fl) => fl.rooms.map((r) => (r.label || r.type || "Room").toUpperCase())));
      const texts = [...document.querySelectorAll("#vista2d text")].map((t) => t.textContent);
      const sprites3d = c.P.flatMap((F) => F.labels).filter((sp) => sp.visible).map((sp) => sp.userData.room.label || sp.userData.room.type);
      const tags = c.P.filter((F) => F.tag.visible).length, stairs = c.escG.filter((e) => e.etq && e.etq.visible).length;
      return { texts, bad: texts.filter((t) => !names.has(t)), sprites3d, tags, stairs, incSprites: c.incSprites(), inspector: getComputedStyle(document.getElementById("inspector")).display };
    });
    if (!info.bad.length && info.texts.length <= 4) ok(`2D texts: ${info.texts.join(", ") || "none"}`); else fail(`2D texts: ${JSON.stringify(info.texts)}`);
    if (info.sprites3d.length <= 4 && !info.tags && !info.stairs && !info.incSprites) ok(`3D labels: ${info.sprites3d.join(", ") || "none"}, no incident or floor text`); else fail(`3D texts: ${JSON.stringify(info)}`);

    // room tap -> route only; tap again clears; tap outside clears
    const route = () => f.evaluate(() => ({ d2: document.querySelectorAll("#vista2d polyline").length, d3: window.__casa.refs.rutaG.children.length, insp: getComputedStyle(document.getElementById("inspector")).display }));
    await f.locator('#plantas3d [data-foco="0"]').tap(); await page.waitForTimeout(500);
    await f.locator(`#vista2d [data-id="${ROOM}"]`).tap({ force: true }); await page.waitForTimeout(500);
    let r = await route();
    const roomText = await f.evaluate(() => [...document.querySelectorAll("#vista2d text")].map((t) => t.textContent));
    if (r.d2 > 0 && r.d3 > 0 && r.insp === "none") ok(`room tap: route in 2D and 3D, no panel (labels: ${roomText.join(", ")})`); else fail(`room tap: ${JSON.stringify(r)}`);
    const viewerText2 = await f.evaluate(() => document.body.innerText);
    if (!/sq ft|m²|Distance|Nearest exit/i.test(viewerText2)) ok("room tap: no area or info text"); else fail("room tap shows info text");
    await f.locator(`#vista2d [data-id="${ROOM}"]`).tap({ force: true }); await page.waitForTimeout(500);
    r = await route();
    if (r.d2 === 0 && r.d3 === 0) ok("tap again: route cleared"); else fail(`tap again: ${JSON.stringify(r)}`);
    await f.locator(`#vista2d [data-id="${ROOM}"]`).tap({ force: true }); await page.waitForTimeout(400);
    await f.locator("#vista2d").tap({ position: { x: 4, y: 4 }, force: true }); await page.waitForTimeout(500);
    r = await route();
    if (r.d2 === 0 && r.d3 === 0) ok("tap outside: route cleared"); else fail(`tap outside: ${JSON.stringify(r)}`);
    // a room without a route (if the plan has one): one-line notice for about 2 s
    const findNoRoute = () => f.evaluate(() => { const c = window.__casa; for (const [fi, fl] of c.S.plan.floors.entries()) for (const rm of fl.rooms) if (c.S.ev && !c.S.ev.rutas.get(c.S.ev.clave(fi, rm)) && document.querySelector(`#vista2d [data-id="${rm.id}"]`)) return rm.id; return null; });
    let noRoute = await findNoRoute();
    if (!noRoute) { await f.locator('#plantas3d [data-foco="1"]').tap(); await page.waitForTimeout(500); noRoute = await findNoRoute(); }
    // Fairdale has a route from every room: drop one room's route from the evacuation graph (test only) to exercise the notice
    if (!noRoute) noRoute = await f.evaluate(() => { const c = window.__casa, fi = c.S.foco === "all" ? c.S.f2d : c.S.foco;
      const rm = c.S.plan.floors[fi].rooms.find((r) => document.querySelector(`#vista2d path[data-id="${r.id}"]`)); if (!rm) return null;
      c.S.ev.rutas.delete(c.S.ev.clave(fi, rm)); return rm.id; });
    if (noRoute) {
      // click on the room's own path (its center may lie on a smaller room drawn on top)
      await f.evaluate((id) => document.querySelector(`#vista2d path[data-id="${id}"]`).dispatchEvent(new MouseEvent("click", { bubbles: true })), noRoute); await page.waitForTimeout(300);
      const shown = await visibleBox(f, "#aviso-ruta"), txt = shown ? await f.locator("#aviso-ruta").innerText() : "";
      await page.waitForTimeout(2300);
      const gone = !(await visibleBox(f, "#aviso-ruta"));
      if (shown && !txt.includes("\n") && gone) ok(`no-route room: "${txt}" for ~2 s`); else fail(`no-route notice: shown=${!!shown} text=${txt} gone=${gone}`);
      await f.locator("#vista2d").tap({ position: { x: 4, y: 4 }, force: true });
    } else ok("no-route notice: every room on this floor has a route (not exercised)");
    // floor selector kept
    await f.locator('#plantas3d [data-foco="all"]').tap(); await page.waitForTimeout(300);
    if ((await f.locator("#plantas3d [data-foco]").count()) >= 3 && (await f.locator('#plantas3d [data-foco="all"].on').count())) ok("floor selector ALL / 1 / 2"); else fail("floor selector");

    const small = [...(await tooSmall(page.mainFrame(), "[data-house-mdt]")), ...(await tooSmall(f))]; // app navigation: not this screen
    if (!small.length) ok("touch targets >= 44 px"); else small.slice(0, 8).forEach((s) => fail(`target under 44 px: ${s}`));
    if (!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1))) ok("no horizontal scroll"); else fail("horizontal scroll");
    const bad = errors.filter((e) => !["404", "/ws/calls"].some((a) => e.includes(a)));
    if (!bad.length) ok(`console clean${errors.length ? ` (${errors.length} expected)` : ""}`); else bad.forEach((e) => fail(`console: ${e.slice(0, 160)}`));
    await ctx.close();
  }
  await browser.close();
} finally {
  mock.kill();
  await vite.close();
}
if (MEASURE) { console.log("measured:", JSON.stringify(measured)); process.exit(0); }
console.log(failures ? `${failures} FAILED` : "OK house MDT (webkit, 1180x820 and 1024x768)");
process.exit(failures ? 1 : 0);
