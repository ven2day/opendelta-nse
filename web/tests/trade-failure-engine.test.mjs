import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("Strong Buy exposes a research-only Failure Engine comparison", async () => {
  const [dashboard, results, dockerfile] = await Promise.all([
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/strong-buy-results.tsx", root), "utf8"),
    readFile(new URL("deploy/backtest.Dockerfile", root), "utf8"),
  ]);

  assert.match(dashboard, /failureEngineMode: "OFF"/);
  assert.match(dashboard, /RESEARCH_COMPARE/);
  assert.match(dashboard, /Chronological walk-forward/);
  assert.match(dashboard, /Never live/);
  assert.match(dashboard, /strongBuySettings\.failureEngineMode === "RESEARCH_COMPARE"/);
  assert.match(results, /Trade Failure Engine walk-forward comparison/);
  assert.match(results, /Research only · live exits disabled/);
  assert.match(results, /Baseline: hold to target\/horizon/);
  assert.match(dockerfile, /trade_failure_engine\.py/);
});
