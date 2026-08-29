import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("manual refresh exposes progress and distinguishes market close from disconnect", async () => {
  const dashboard = await readFile(new URL("app/dashboard.tsx", root), "utf8");

  assert.match(dashboard, /processedSymbols: number \| null/);
  assert.match(dashboard, /totalSymbols: number \| null/);
  assert.match(dashboard, /Refreshing \$\{status\.processedSymbols\}\/\$\{status\.totalSymbols\} symbols/);
  assert.match(dashboard, /nextMarketSession === "CLOSED" \? "MARKET_CLOSED"/);
  assert.match(dashboard, /\.replace\(\/_\/g, " "\)/);
  assert.match(dashboard, /aria-live="polite"/);
  assert.doesNotMatch(dashboard, /symbols refreshed/);
  assert.match(dashboard, /return formatDhanTimestamp\(timestamp\)/);
});

test("closed sessions do not mark a settled snapshot as stale", async () => {
  const [dashboard, styles] = await Promise.all([
    readFile(new URL("app/dashboard.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);

  assert.match(dashboard, /marketSession !== "CLOSED" && isMarketDataStale/);
  assert.match(dashboard, /marketSession === "CLOSED" && refreshStatus\.state !== "FAILED" \? "market-closed"/);
  assert.match(styles, /\.dhan-refresh-control\.market-closed \.status-dot/);
  assert.match(styles, /\.dhan-refresh-control\.refreshing \.status-dot/);
});

test("dashboard can add Dhan-validated symbols and exposes company names on hover", async () => {
  const [dashboard, parser, route, styles] = await Promise.all([
    readFile(new URL("app/dashboard.tsx", root), "utf8"),
    readFile(new URL("app/market-data.ts", root), "utf8"),
    readFile(new URL("app/api/market-symbols/route.ts", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
  ]);

  assert.match(dashboard, /fetch\("\/api\/market-symbols"/);
  assert.match(dashboard, /title=\{stock\.company_name \?\? stock\.symbol\}/);
  assert.match(dashboard, /stock\.company_name/);
  assert.match(parser, /company_name: string \| null/);
  assert.match(route, /\/market-data\/symbols/);
  assert.match(styles, /\.symbol-add-form/);
  assert.match(styles, /grid-template-columns: minmax\(120px, 1fr\) auto/);
});
