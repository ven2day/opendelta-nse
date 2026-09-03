import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("admin exposes one persisted inclusive global price range", async () => {
  const [page, form, api, backend] = await Promise.all([
    source("app/admin/page.tsx"),
    source("app/admin/admin-settings.tsx"),
    source("app/api/global-settings/route.ts"),
    source("../backend/config/application_settings.py"),
  ]);
  assert.match(page, /requireSessionUser/);
  assert.match(form, /Global minimum price/);
  assert.match(form, /Global maximum price/);
  assert.match(form, /step="0\.01"/);
  assert.match(form, /minimum price must be less than maximum price/i);
  assert.match(form, /Reset to all prices/);
  assert.match(api, /getSessionUser/);
  assert.match(api, /export function PUT\(request: Request\)/);
  assert.match(backend, /application-settings\.sqlite3/);
  assert.match(backend, /minimum_price <= numeric <= self\.maximum_price/);
});

test("dashboard, signals and backtest share the same server-defined range", async () => {
  const [dashboard, home, signals, signalsPage, backtest, backtestPage, shared] = await Promise.all([
    source("app/legacy/screener/dashboard.tsx"), source("app/legacy/screener/page.tsx"),
    source("app/legacy/signals/signals-workspace.tsx"), source("app/legacy/signals/page.tsx"),
    source("app/legacy/backtest/backtest-dashboard.tsx"), source("app/legacy/backtest/page.tsx"),
    source("app/global-settings-shared.ts"),
  ]);
  assert.match(home, /readGlobalSettings/);
  assert.match(dashboard, /marketStocks\.filter\(\(stock\) => isPriceInGlobalRange/);
  assert.match(signalsPage, /readGlobalSettings/);
  assert.match(signals, /item\.currentPrice \?\? item\.signalClose/);
  assert.doesNotMatch(signals, /paperTrades\.filter\([\s\S]*isPriceInGlobalRange/);
  assert.match(backtestPage, /isPriceInGlobalRange\(row\.entry_price/);
  assert.match(backtest, /fetch\("\/api\/market-symbols"/);
  assert.match(backtest, /current\.filter\(\(symbol\) => next\.includes\(symbol\)\)/);
  assert.match(shared, /price >= range\.minimumPrice/);
  assert.match(shared, /price <= range\.maximumPrice/);
});

test("default all-price setting preserves existing visibility and every shell links Admin", async () => {
  const files = await Promise.all([
    source("app/legacy/screener/dashboard.tsx"),
    source("app/legacy/backtest/backtest-dashboard.tsx"),
    source("app/legacy/signals/signals-workspace.tsx"),
    source("app/legacy/signals/live-universe.tsx"),
  ]);
  files.forEach((file) => assert.match(file, /href="\/admin"/));
  const shared = await source("app/global-settings-shared.ts");
  assert.match(shared, /minimumPrice: GLOBAL_PRICE_MINIMUM/);
  assert.match(shared, /maximumPrice: GLOBAL_PRICE_MAXIMUM/);
});

test("global settings layout does not create page-level horizontal overflow", async () => {
  const styles = await source("app/globals.css");
  assert.match(styles, /\.admin-price-grid[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.admin-price-grid > label[\s\S]*min-width: 0/);
  assert.match(styles, /\.admin-price-input input[\s\S]*width: 100%/);
  assert.match(styles, /@media \(max-width: 620px\)[\s\S]*\.admin-price-grid \{ grid-template-columns: 1fr/);
  assert.match(styles, /\.top-nav[\s\S]*max-width: 100%/);
  assert.match(styles, /\.nav-item[\s\S]*min-width: 0/);
});
