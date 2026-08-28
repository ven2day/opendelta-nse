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

test("the last ten completed results use persistent browser storage and restore after refresh", async () => {
  const [dashboard, history] = await Promise.all([
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/backtest-history.ts", root), "utf8"),
  ]);

  assert.match(history, /export const BACKTEST_HISTORY_LIMIT = 10/);
  assert.match(history, /indexedDB\.open\(DATABASE_NAME, DATABASE_VERSION\)/);
  assert.match(history, /slice\(0, BACKTEST_HISTORY_LIMIT - 1\)/);
  assert.match(history, /store\.delete\(item\.id\)/);
  assert.match(dashboard, /readBacktestHistory<BacktestResponse \| RecoveryBacktestResponse>\(\)/);
  assert.match(dashboard, /setResponse\(latest\.response\)/);
  assert.match(dashboard, /saveBacktestHistory\(stored\)/);
  assert.match(dashboard, /Latest 10 completed results/);
  assert.match(dashboard, /View result/);
});
