import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const siteDirectory = path.resolve(scriptDirectory, "..");
const sourceFile = path.resolve(siteDirectory, "..", "nse_symbols_rsi_volume.csv");
const dataDirectory = path.resolve(siteDirectory, "app", "data");
const publicDirectory = path.resolve(siteDirectory, "public");
const liveDirectory = path.resolve(publicDirectory, "live");

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];

    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += character;
    }
  }

  values.push(current);
  return values;
}

const numericColumns = new Set([
  "rank",
  "previous_close",
  "entry_price",
  "change_percent",
  "previous_rsi_14",
  "rsi_14",
  "volume_24h",
  "support_1_price",
  "support_2_price",
  "resistance_1_price",
  "resistance_2_price",
]);

const csv = await readFile(sourceFile, "utf8");
const sourceStats = await stat(sourceFile);
const lines = csv.trim().split(/\r?\n/);
const headers = parseCsvLine(lines[0]);
const rows = lines.slice(1).map((line) => {
  const values = parseCsvLine(line);

  return Object.fromEntries(
    headers.map((header, index) => {
      const value = values[index] ?? "";
      if (value === "") return [header, null];
      return [header, numericColumns.has(header) ? Number(value) : value];
    }),
  );
});

const sessions = rows
  .map((row) => row.trading_date)
  .filter((value) => typeof value === "string");

const payload = {
  generatedAt: `${new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(sourceStats.mtime).replace(" ", "T")}+05:30`,
  latestSession: sessions.sort().at(-1) ?? null,
  totalSymbols: rows.length,
  rows,
};

await mkdir(dataDirectory, { recursive: true });
await mkdir(publicDirectory, { recursive: true });
await mkdir(liveDirectory, { recursive: true });
await writeFile(
  path.join(dataDirectory, "nse-data.json"),
  `${JSON.stringify(payload)}\n`,
  "utf8",
);
await copyFile(sourceFile, path.join(publicDirectory, "nse_symbols_rsi_volume.csv"));
await copyFile(sourceFile, path.join(liveDirectory, "nse_symbols_rsi_volume.csv"));

console.log(`Synced ${rows.length} NSE symbols for the dashboard.`);
