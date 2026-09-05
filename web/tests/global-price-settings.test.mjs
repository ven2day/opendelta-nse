import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("the legacy global price contract remains secured but is not a settings-page control", async () => {
  const [page, form, api, backend] = await Promise.all([
    source("app/settings/page.tsx"),
    source("app/settings/price-range-form.tsx"),
    source("app/api/global-settings/route.ts"),
    source("../backend/config/application_settings.py"),
  ]);
  assert.match(page, /requireSessionUser/);
  assert.doesNotMatch(page, /readGlobalSettings|GlobalPriceRangeForm/);
  assert.match(form, /Global minimum price/);
  assert.match(form, /Global maximum price/);
  assert.match(form, /step="0\.01"/);
  assert.match(form, /minimum price must be less than maximum price/i);
  assert.match(form, /Reset to all prices/);
  assert.match(form, /fetch\("\/api\/global-settings"/);
  assert.match(api, /getSessionUser/);
  assert.match(api, /export function PUT\(request: Request\)/);
  assert.match(backend, /application-settings\.sqlite3/);
  assert.match(backend, /minimum_price <= numeric <= self\.maximum_price/);
});

test("the shared range contract is inclusive and bounded", async () => {
  const shared = await source("app/global-settings-shared.ts");
  assert.match(shared, /price >= range\.minimumPrice/);
  assert.match(shared, /price <= range\.maximumPrice/);
  assert.match(shared, /minimumPrice: GLOBAL_PRICE_MINIMUM/);
  assert.match(shared, /maximumPrice: GLOBAL_PRICE_MAXIMUM/);
});

test("the retired /admin route redirects into the unified Settings workspace", async () => {
  const page = await source("app/admin/page.tsx");
  assert.match(page, /requireSessionUser/);
  assert.match(page, /redirect\("\/settings"\)/);
});

test("global settings layout does not create page-level horizontal overflow", async () => {
  const styles = await source("app/globals.css");
  assert.match(styles, /\.admin-price-grid[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.admin-price-grid > label[\s\S]*min-width: 0/);
  assert.match(styles, /\.admin-price-input input[\s\S]*width: 100%/);
  assert.match(styles, /@media \(max-width: 620px\)[\s\S]*\.admin-price-grid \{ grid-template-columns: 1fr/);
});
