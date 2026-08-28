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
  assert.match(dashboard, /Last refresh ·/);
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
