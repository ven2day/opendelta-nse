import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("backtest selector refreshes from the managed runtime symbol registry", async () => {
  const [dashboard, route] = await Promise.all([
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/api/market-symbols/route.ts", root), "utf8"),
  ]);

  assert.match(route, /export async function GET\(request: Request\)/);
  assert.match(route, /fetch\(`\$\{service\}\/market-data\/symbols`/);
  assert.match(dashboard, /fetch\("\/api\/market-symbols", \{ cache: "no-store" \}\)/);
  assert.match(dashboard, /setAvailableSymbols\(next\)/);
  assert.match(dashboard, /useAllSymbols \? availableSymbols : selectedSymbols/);
  assert.match(dashboard, /All \{availableSymbols\.length\} symbols/);
});

test("the last ten completed results sync to the signed-in account with browser migration and cache fallback", async () => {
  const [dashboard, history, route, owner] = await Promise.all([
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/backtest-history.ts", root), "utf8"),
    readFile(new URL("app/api/backtest-history/route.ts", root), "utf8"),
    readFile(new URL("app/api/history-owner.ts", root), "utf8"),
  ]);

  assert.match(history, /export const BACKTEST_HISTORY_LIMIT = 10/);
  assert.match(history, /indexedDB\.open\(DATABASE_NAME, DATABASE_VERSION\)/);
  assert.match(history, /slice\(0, BACKTEST_HISTORY_LIMIT - 1\)/);
  assert.match(history, /store\.delete\(item\.id\)/);
  assert.match(history, /fetch\("\/api\/backtest-history"/);
  assert.match(history, /migrateBrowserBacktestHistory/);
  assert.match(route, /getSessionUser\(\)/);
  assert.match(route, /historyOwnerKey\(username\)/);
  assert.match(owner, /crypto\.subtle\.digest/);
  assert.match(route, /x-opendelta-history-owner/);
  assert.match(route, /export async function GET/);
  assert.match(route, /export async function POST/);
  assert.match(dashboard, /readBacktestHistory<BacktestResponse \| RecoveryBacktestResponse>\(\)/);
  assert.match(dashboard, /migrateBrowserBacktestHistory\(browserEntries\)/);
  assert.match(dashboard, /readAccountBacktestResult<BacktestResponse \| RecoveryBacktestResponse>/);
  assert.match(dashboard, /saveBacktestHistory\(stored\)/);
  assert.match(dashboard, /saveAccountBacktestHistory\(stored\)/);
  assert.match(dashboard, /Latest 10 completed results/);
  assert.match(dashboard, /available in every signed-in browser/);
  assert.match(dashboard, /View result/);
});

test("long backtests report trading and supporting progress and retry the symbol registry", async () => {
  const [dashboard, route] = await Promise.all([
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/api/backtest/route.ts", root), "utf8"),
  ]);

  assert.match(dashboard, /supportSymbolsCompleted/);
  assert.match(dashboard, /SUPPORTING_MARKET_FEATURES/);
  assert.match(dashboard, /supporting symbols/);
  assert.match(dashboard, /window\.setTimeout\(loadSymbols, 5_000\)/);
  assert.match(route, /x-opendelta-history-owner/);
  assert.match(route, /historyOwnerKey\(sessionUser\)/);
});
