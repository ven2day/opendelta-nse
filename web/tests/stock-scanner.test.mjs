import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("Stock Scanner is directly beside Dashboard in every application shell", async () => {
  const files = await Promise.all([
    source("app/dashboard.tsx"),
    source("app/backtest/backtest-dashboard.tsx"),
    source("app/signals/signals-workspace.tsx"),
    source("app/signals/live-universe.tsx"),
    source("app/admin/admin-settings.tsx"),
  ]);
  for (const file of files) {
    assert.match(file, /Dashboard[\s\S]{0,60}<\/a>[\s\S]{0,180}href="\/scanner"/);
    assert.match(file, /Stock Scanner/);
  }
});

test("scanner page and API require the existing authenticated session", async () => {
  const [page, route] = await Promise.all([
    source("app/scanner/page.tsx"),
    source("app/api/stock-scanner/route.ts"),
  ]);
  assert.match(page, /requireSessionUser\(\)/);
  assert.match(route, /getSessionUser\(\)/);
  assert.match(route, /Authentication required/);
  assert.match(route, /\/stock-scanner\?refresh=/);
  assert.match(route, /cache: "no-store"/);
});

test("scanner UI exposes Top 2 primary, ranks 3-5 reserve and Top 20 opportunities", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /Top 2 PRIMARY/);
  assert.match(scanner, /ranks 3–5 RESERVE/);
  assert.match(scanner, /Top 20 opportunities/);
  assert.match(scanner, /response\.watchlist\.topFive/);
  assert.match(scanner, /response\.opportunities/);
});

test("scanner shows 15-minute history and explicit 09:30-14:30 research cadence", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /15-minute rescans/);
  assert.match(scanner, /09:30–14:30 IST/);
  assert.match(scanner, /Intraday watchlist history/);
  assert.match(scanner, /promoted/);
  assert.match(scanner, /removed/);
});

test("scanner consumes the application-wide price filter", async () => {
  const [scanner, backend] = await Promise.all([
    source("app/scanner/stock-scanner.tsx"),
    source("../backtest_api.py"),
  ]);
  assert.match(scanner, /Global price range/);
  assert.match(scanner, /metadata\.globalPriceRange/);
  assert.match(backend, /market_symbols = list_market_symbols\(\)/);
  assert.match(backend, /minimum_price=settings\.minimum_price/);
  assert.match(backend, /maximum_price=settings\.maximum_price/);
});

test("scanner stays paper-only and cannot modify RSI Recovery signals", async () => {
  const [scanner, backend] = await Promise.all([
    source("app/scanner/stock-scanner.tsx"),
    source("../stock_scanner.py"),
  ]);
  assert.match(scanner, /never places broker orders/);
  assert.match(scanner, /frozen RSI Recovery signal universe/);
  assert.match(backend, /"liveOrdersEnabled": False/);
  assert.match(backend, /"signalUniversePolicy": "FROZEN_AT_09_30"/);
  assert.doesNotMatch(backend, /place_order|broker_order|paper-buy/);
});

test("company names are available on symbol hover", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /title=\{entry\.companyName\}/);
  assert.match(scanner, /\{entry\.companyName\}/);
});

test("responsive scanner layout contains wide tables without page overflow", async () => {
  const styles = await source("app/globals.css");
  assert.match(styles, /\.scanner-shell[\s\S]*overflow-x: clip/);
  assert.match(styles, /\.scanner-table-wrap[\s\S]*overflow: clip/);
  assert.match(styles, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
  assert.match(styles, /@media \(max-width: 760px\)[\s\S]*\.scanner-status-grid[\s\S]*grid-template-columns: 1fr/);
});

test("production backend image includes the scanner module", async () => {
  const dockerfile = await source("deploy/backtest.Dockerfile");
  assert.match(dockerfile, /stock_scanner\.py/);
});
