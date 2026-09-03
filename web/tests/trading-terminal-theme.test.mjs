import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

function luminance(hex) {
  const channels = [1, 3, 5]
    .map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground, background) {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function darkToken(styles, name) {
  const block = styles.match(/\.platform-frame,\s*\.platform-frame\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/)?.[1];
  assert.ok(block, "dark terminal token block must exist");
  const value = block.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "i"))?.[1];
  assert.ok(value, `${name} must be a six-digit colour token`);
  return value;
}

test("the terminal theme is authoritative and dark-first", async () => {
  const [layout, styles] = await Promise.all([
    source("app/layout.tsx"),
    source("app/platform/trading-terminal.css"),
  ]);

  assert.ok(layout.indexOf('import "./globals.css"') < layout.indexOf('import "./platform/trading-terminal.css"'));
  assert.match(styles, /\.platform-frame,\s*\.platform-frame\[data-theme="dark"\]/);
  assert.match(styles, /\.platform-frame\[data-theme="light"\]/);
  assert.match(styles, /\.platform-frame \.site-shell/);
  assert.doesNotMatch(styles, /linear-gradient|radial-gradient/);
});

test("dark terminal text and semantic colours remain readable", async () => {
  const styles = await source("app/platform/trading-terminal.css");
  const canvas = darkToken(styles, "od-canvas");
  const surface = darkToken(styles, "od-surface");
  const accent = darkToken(styles, "od-accent");

  for (const token of ["od-text", "od-muted", "od-faint", "od-success", "od-warning", "od-danger"]) {
    assert.ok(contrast(darkToken(styles, token), surface) >= 4.5, `${token} must meet 4.5:1 contrast on panels`);
  }
  assert.ok(contrast(accent, canvas) >= 4.5, "accent must be readable on the application canvas");
  assert.ok(contrast(darkToken(styles, "od-accent-contrast"), accent) >= 4.5, "button text must be readable on the accent");
});

test("the six workspaces use the shared terminal primitives", async () => {
  const [styles, signals] = await Promise.all([
    source("app/platform/trading-terminal.css"),
    source("app/signals/signals-workspace.tsx"),
  ]);
  for (const selector of [
    ".quant-workspace",
    ".quant-panel",
    ".quant-table th",
    ".quant-table td",
    ".quant-form-grid input",
    '.quant-signal-status[data-colour="green"]',
    ".quant-pnl.negative",
  ]) {
    assert.ok(styles.includes(selector), `${selector} must be styled by the terminal theme`);
  }

  const pixelSizes = [...styles.matchAll(/font-size:\s*([0-9.]+)px/g)].map((match) => Number(match[1]));
  assert.ok(pixelSizes.length > 0);
  assert.equal(pixelSizes.filter((size) => size < 11).length, 0, "terminal text must never be smaller than 11px");
  assert.match(styles, /data-colour="blue"[^}]*var\(--od-accent\)/);
  assert.match(styles, /\.platform-frame \.quant-form-actions button\.primary/);
  assert.match(styles, /\.quant-dashboard-grid/);
  assert.match(styles, /\.quant-screener-layout/);
  assert.match(signals, /Gold is a fresh strong buy/);
});
