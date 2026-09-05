import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const stylesUrl = new URL("../app/platform/trading-terminal.css", import.meta.url);

test("shared workspaces use the compact production density contract", async () => {
  const styles = await readFile(stylesUrl, "utf8");
  assert.match(styles, /\.quant-workspace\s*\{[^}]*gap:\s*8px;[^}]*padding:\s*12px 0 24px;/s);
  assert.match(styles, /\.quant-panel-heading\s*\{[^}]*min-height:\s*46px;[^}]*padding:\s*9px 12px;/s);
  assert.match(styles, /\.quant-panel-body\s*\{\s*padding:\s*10px 12px;/);
  assert.match(styles, /\.quant-table td\s*\{[^}]*padding:\s*8px 10px;/s);
  assert.match(styles, /\.quant-state\.empty\s*\{[^}]*min-height:\s*96px;/s);
});

test("market and section tabs share one aligned control height", async () => {
  const styles = await readFile(stylesUrl, "utf8");
  assert.match(styles, /\.platform-market-switch\s*\{[^}]*height:\s*40px;[^}]*align-items:\s*center;/s);
  assert.match(styles, /\.platform-market-switch a\s*\{[^}]*height:\s*34px;[^}]*align-items:\s*center;/s);
  assert.match(styles, /\.quant-market-tabs,[\s\S]*?\.quant-section-tabs\s*\{[^}]*min-height:\s*34px;[^}]*align-items:\s*stretch;/s);
  assert.match(styles, /\.quant-section-tabs button\s*\{[^}]*min-height:\s*28px;[^}]*align-items:\s*center;[^}]*line-height:\s*1;/s);
});
