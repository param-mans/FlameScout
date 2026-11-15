// House catalog id resolution (house-catalog.mjs + handler.mjs), offline: no keys, no network, temp files only.
//   node --test apps/web/server/tests/catalog.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHouseDataHandler } from "../handler.mjs";
import { createCatalogLookup, parseCsv } from "../house-catalog.mjs";

const HEAD = "listing_id,matterport_model_id,address,city,state,zip,floors,bedrooms,bathrooms,sqft,year_built,room_types,exterior_doors,stairs,quality";
function setup() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fs-catalog-"));
  const samples = path.join(dir, "index.json");
  fs.writeFileSync(samples, JSON.stringify([{ id: "sample-z", address: "1 Test St, Houston", location: null, city: "Houston", market: "Houston", matterportModelId: null }]));
  fs.mkdirSync(path.join(dir, "raw"));
  fs.writeFileSync(path.join(dir, "raw", "catalog.csv"), `${HEAD}\n111222333,AbCdEf12345,"12 Elm Street, Rear",Houston,TX,77001,2,3,2.5,1800,1999,bathroom|kitchen,2,1,ok\n`);
  const handle = createHouseDataHandler({ getEnv: () => undefined, catalogFile: samples, storedDir: path.join(dir, "raw") });
  return { dir, handle };
}
function get(handle, url) {
  return new Promise((resolve) => {
    const res = { statusCode: 200, headers: {}, setHeader(k, v) { this.headers[k] = v; },
      end(body) { resolve({ status: this.statusCode, body: JSON.parse(body) }); } };
    handle({ method: "GET", url, headers: {}, socket: { remoteAddress: "t" } }, res, () => resolve({ status: "next" }));
  });
}

test("parseCsv handles quoted commas", () => {
  const [row] = parseCsv(`${HEAD}\n1,M1,"A, B",C,TX,1,1,1,1,1,1,x|y,0,0,ok\n`);
  assert.equal(row.address, "A, B");
  assert.equal(row.room_types, "x|y");
});

test("catalog lookup maps a row to a sample entry; unknown ids and a missing file give null", () => {
  const { dir } = setup();
  const lookup = createCatalogLookup(path.join(dir, "raw", "catalog.csv"));
  const s = lookup("111222333");
  assert.equal(s.matterportModelId, "AbCdEf12345");
  assert.equal(s.address, "12 Elm Street, Rear, Houston");
  assert.deepEqual(s.listing, { beds: 3, baths: 2.5, sqft: 1800, yearBuilt: 1999, homeType: null });
  assert.equal(lookup("999"), null);
  assert.equal(createCatalogLookup(path.join(dir, "nope.csv"))("111222333"), null);
});

test("the handler resolves a catalog listing id, still serves samples, and refuses other ids", async () => {
  const { handle } = setup();
  // catalog id: resolved; without Matterport credentials or a stored plan the plan cannot be built (503, not 404)
  const cat = await get(handle, "/api/house-viewer/sample/111222333");
  assert.equal(cat.status, 503);
  assert.match(cat.body.error, /token is not set/);
  assert.equal((await get(handle, "/api/house-viewer/plan/111222333")).status, 503);
  // samples/index.json id: served as before (no scan sample)
  const smp = await get(handle, "/api/house-viewer/sample/sample-z");
  assert.equal(smp.status, 200);
  assert.equal(smp.body.no_scan, true);
  assert.equal((await get(handle, "/api/house-viewer/plan/sample-z")).status, 404);
  // anything else: unknown
  for (const id of ["444555666", "abcdef12345"]) {
    assert.equal((await get(handle, `/api/house-viewer/sample/${id}`)).status, 404);
    assert.equal((await get(handle, `/api/house-viewer/plan/${id}`)).status, 404);
  }
  // a catalog house has no coordinates: no Vexcel
  assert.equal((await get(handle, "/api/house-viewer/vexcel/111222333")).status, 404);
});
