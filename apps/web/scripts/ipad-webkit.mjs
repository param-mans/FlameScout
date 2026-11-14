// iPad checks with WebKit (Safari's engine) and touch: taps only, no mouse.
//   cd apps/web && npm i --no-save playwright && npx playwright install webkit   (package.json is not touched)
//   npm run dev   (in another terminal)
//   node scripts/ipad-webkit.mjs [--base http://localhost:5173] [--shots ../../docs/capturas/ui]
// For each landscape size: choose a floor, open LAYERS, tap a room, "Show on plan", switch the Vexcel tab and, with
// ?debug=1, switch the incident scenario. Every visible interactive element must be at least 44 x 44 px; no horizontal
// page scroll (also in portrait); no console errors. Exit code 1 on any failure.
import { webkit } from "playwright";
import fs from "node:fs";
import path from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("--base", "http://localhost:5173"), SHOTS = arg("--shots", null);
const LANDSCAPE = [["ipad-pro-11", 1180, 820], ["ipad-pro-12.9", 1366, 1024], ["ipad-10.2", 1080, 810], ["1024x768", 1024, 768]];
const PORTRAIT = [["ipad-pro-11-portrait", 820, 1180], ["ipad-10.2-portrait", 810, 1080]];
const HOUSE = "209217448", ROOM = "d3slsxy3zlg"; // Fairdale; a bedroom with a route to an exterior door
const INTERACTIVE = "button, a[href], input:not([type=hidden]), select, summary, [role=button]";
let failures = 0;
const fail = (m) => { failures++; console.log("  FAIL", m); };

// visible interactive elements smaller than 44 x 44 (an input inside a label is measured by its label)
async function tooSmall(frame, where) {
  return frame.evaluate(({ sel, where }) => {
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      const target = el.matches("input") && el.closest("label") ? el.closest("label") : el;
      const r = target.getBoundingClientRect(), cs = getComputedStyle(target);
      if (!r.width || !r.height || cs.visibility === "hidden" || cs.display === "none" || target.closest("[hidden]")) continue;
      if (el.closest("details:not([open])") && !el.matches("summary")) continue;
      if (r.width < 44 || r.height < 44) out.push(`${where}: <${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}> "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 24)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return out;
  }, { sel: INTERACTIVE, where });
}

const browser = await webkit.launch();
for (const [name, w, h] of [...LANDSCAPE, ...PORTRAIT]) {
  console.log(`== ${name} ${w}x${h}`);
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  const page = await ctx.newPage(), errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/?sample=${HOUSE}&debug=1`, { waitUntil: "load" });
  const v = page.frameLocator("iframe"), vf = () => page.frames().find((f) => f.url().includes("casa.html")), small = new Set();
  const measure = async (state) => { for (const s of [...(await tooSmall(page.mainFrame(), `page/${state}`)), ...(await tooSmall(vf(), `viewer/${state}`))]) small.add(s); };
  try { await v.locator("#pisos-split [data-foco]").first().waitFor({ state: "visible", timeout: 30000 }); } catch { fail("viewer did not load"); await ctx.close(); continue; }
  await page.waitForTimeout(1500);
  const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (hscroll) fail("horizontal page scroll");
  if (!PORTRAIT.some((p) => p[0] === name)) {
    let step = "floor";
    try {
      await v.locator('#pisos-split [data-foco="1"]').tap(); await page.waitForTimeout(400);
      await v.locator('#pisos-split [data-foco="0"]').tap(); await page.waitForTimeout(400);
      step = "layers"; await v.locator("#btn-capas").tap(); await page.waitForTimeout(300);
      if (!(await v.locator("#izq.abierto").count())) fail("LAYERS did not open");
      await measure("layers");
      await v.locator("#btn-capas").tap(); await page.waitForTimeout(300);
      step = "room"; await v.locator(`#vista2d [data-id="${ROOM}"]`).tap({ force: true }); await page.waitForTimeout(600);
      if (!(await v.locator("#inspector h3").count())) fail("tapping a room did not open the inspector");
      await measure("inspector");
      step = "show on plan"; await v.locator("#ver-salida").tap(); await page.waitForTimeout(500);
      if (!(await v.locator("#vista2d circle animate").count())) fail("Show on plan did not highlight the exit door");
      step = "close inspector"; await v.locator("#cerrar-insp").tap(); await page.waitForTimeout(300);
      step = "vexcel"; if (await v.locator("#der-tabs").isVisible()) { await v.locator('#der-tabs [data-der="entorno"]').tap(); await page.waitForTimeout(400); }
      await v.locator('#oblicuas-body [data-obl="north"]').tap(); await page.waitForTimeout(400);
      if (!(await v.locator('#oblicuas-body [data-obl="north"].on').count())) fail("Vexcel N tab did not switch");
      if (await v.locator("#der-tabs").isVisible()) await v.locator('#der-tabs [data-der="plan"]').tap();
      step = "debug scenario"; await v.locator("#inc-debug [data-tog]").tap(); await page.waitForTimeout(200);
      await v.locator('[data-esc="S4"]').tap(); await page.waitForTimeout(1200);
      if (!/Smoke|Fire/.test(await page.locator(".hv-card").first().innerText())) fail("debug scenario S4 did not reach the incident card");
      await v.locator("#inc-debug [data-tog]").tap(); await page.waitForTimeout(200); // collapse the debug bar again
    } catch (e) { fail(`interaction (${step}): ${String(e).split("\n")[0]}`); }
  }
  await measure("final");
  [...small].slice(0, 12).forEach((s) => fail(`target under 44x44 px: ${s}`));
  if (small.size > 12) fail(`... ${small.size - 12} more targets under 44x44 px`);
  errors.forEach((e) => fail(`console: ${e.slice(0, 160)}`));
  if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `webkit-${name}.png`) }); }
  console.log(`   ${small.size} small targets, ${errors.length} console errors, horizontal scroll: ${hscroll}`);
  await ctx.close();
}
await browser.close();
console.log(failures ? `${failures} FAILED` : "OK iPad checks (WebKit, touch)");
process.exit(failures ? 1 : 0);
