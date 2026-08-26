export type StockRow = {
  rank: number | null;
  symbol: string;
  trading_date: string | null;
  previous_date: string | null;
  previous_close: number | null;
  entry_price: number | null;
  change_percent: number | null;
  previous_rsi_14: number | null;
  rsi_14: number | null;
  volume_24h: number | null;
  support_1_price: number | null;
  support_1_time: string | null;
  support_2_price: number | null;
  support_2_time: string | null;
  resistance_1_price: number | null;
  resistance_1_time: string | null;
  resistance_2_price: number | null;
  resistance_2_time: string | null;
};

export type MarketPayload = {
  latestSession: string | null;
  rows: StockRow[];
};

const numericColumns = new Set<keyof StockRow>([
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

function parseCsvLine(line: string): string[] {
  const values: string[] = [];
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

function numericValue(value: string): number | null {
  if (value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseMarketCsv(csv: string): MarketPayload {
  const lines = csv.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) {
    throw new Error("Market CSV has no data rows");
  }

  const headers = parseCsvLine(lines[0]) as Array<keyof StockRow>;
  const rows = lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const row = Object.fromEntries(
      headers.map((header, index) => {
        const value = values[index] ?? "";
        return [header, numericColumns.has(header) ? numericValue(value) : value || null];
      }),
    ) as StockRow;

    return row;
  });

  const sessionCounts = new Map<string, number>();
  for (const row of rows) {
    if (row.trading_date) {
      sessionCounts.set(
        row.trading_date,
        (sessionCounts.get(row.trading_date) ?? 0) + 1,
      );
    }
  }

  const latestSession = [...sessionCounts].sort(
    ([leftDate, leftCount], [rightDate, rightCount]) =>
      rightCount - leftCount || rightDate.localeCompare(leftDate),
  )[0]?.[0] ?? null;

  return { latestSession, rows };
}
