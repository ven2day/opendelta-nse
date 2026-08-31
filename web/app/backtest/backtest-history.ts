export const BACKTEST_HISTORY_LIMIT = 10;

const DATABASE_NAME = "vento-nse-backtest-history";
const DATABASE_VERSION = 1;
const STORE_NAME = "completed-runs";

export type BacktestHistoryStrategy =
  | "rsi_range"
  | "rsi_recovery"
  | "ema_vwap_strong_buy"
  | "top_5_opening_range_breakout"
  | "daily_scalping_watchlist"
  | "market_aligned_vwap_pullback_scalper"
  | "market_aligned_rsi_scalper";

export type BacktestHistoryEntry<T = unknown> = {
  id: string;
  completedAt: string;
  strategyMode: BacktestHistoryStrategy;
  strategyName: string;
  timeframe: string;
  durationYears: number;
  symbolCount: number;
  response: T;
};

export type BacktestHistorySummary = Omit<BacktestHistoryEntry, "response">;

// Every strategy that may appear in saved history, including the ones retired from new
// runs. Retired results stay readable; only launching them is blocked.
const BACKTEST_HISTORY_STRATEGIES: readonly string[] = [
  "rsi_range",
  "rsi_recovery",
  "ema_vwap_strong_buy",
  "top_5_opening_range_breakout",
  "daily_scalping_watchlist",
  "market_aligned_vwap_pullback_scalper",
  "market_aligned_rsi_scalper",
];

export function backtestHistorySummary<T>(entry: BacktestHistoryEntry<T>): BacktestHistorySummary {
  return {
    id: entry.id,
    completedAt: entry.completedAt,
    strategyMode: entry.strategyMode,
    strategyName: entry.strategyName,
    timeframe: entry.timeframe,
    durationYears: entry.durationYears,
    symbolCount: entry.symbolCount,
  };
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("Browser storage is unavailable"));
  }

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
    request.onerror = () => reject(request.error ?? new Error("Backtest history could not be opened"));
    request.onblocked = () => reject(new Error("Backtest history is open in another browser tab"));
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("Backtest history transaction was cancelled"));
    transaction.onerror = () => reject(transaction.error ?? new Error("Backtest history transaction failed"));
  });
}

function isHistoryEntry(value: unknown): value is BacktestHistoryEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<BacktestHistoryEntry>;
  const response = entry.response as { metadata?: unknown; results?: unknown } | undefined;
  return typeof entry.id === "string"
    && typeof entry.completedAt === "string"
    && BACKTEST_HISTORY_STRATEGIES.includes(entry.strategyMode ?? "")
    && typeof entry.strategyName === "string"
    && typeof entry.timeframe === "string"
    && typeof entry.durationYears === "number"
    && typeof entry.symbolCount === "number"
    && Number.isInteger(entry.symbolCount)
    && entry.symbolCount > 0
    && Boolean(response && typeof response.metadata === "object" && Array.isArray(response.results));
}

function isHistorySummary(value: unknown): value is BacktestHistorySummary {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<BacktestHistorySummary>;
  return typeof entry.id === "string"
    && typeof entry.completedAt === "string"
    && BACKTEST_HISTORY_STRATEGIES.includes(entry.strategyMode ?? "")
    && typeof entry.strategyName === "string"
    && typeof entry.timeframe === "string"
    && typeof entry.durationYears === "number"
    && typeof entry.symbolCount === "number"
    && Number.isInteger(entry.symbolCount)
    && entry.symbolCount > 0;
}

async function responseDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = JSON.parse(await response.text()) as { detail?: unknown };
    return typeof payload.detail === "string" ? payload.detail : fallback;
  } catch {
    return fallback;
  }
}

export async function readAccountBacktestHistory(): Promise<BacktestHistorySummary[]> {
  const response = await fetch("/api/backtest-history", { cache: "no-store" });
  if (!response.ok) throw new Error(await responseDetail(response, "Account backtest history is unavailable"));
  const payload = await response.json() as { runs?: unknown };
  if (!Array.isArray(payload.runs)) throw new Error("Account backtest history is invalid");
  return payload.runs.filter(isHistorySummary).slice(0, BACKTEST_HISTORY_LIMIT);
}

export async function readAccountBacktestResult<T>(id: string): Promise<BacktestHistoryEntry<T>> {
  const response = await fetch(`/api/backtest-history?id=${encodeURIComponent(id)}`, { cache: "no-store" });
  if (!response.ok) throw new Error(await responseDetail(response, "Saved backtest result is unavailable"));
  const payload: unknown = await response.json();
  if (!isHistoryEntry(payload)) throw new Error("Saved backtest result is invalid");
  return payload as BacktestHistoryEntry<T>;
}

export async function saveAccountBacktestHistory<T>(
  entry: BacktestHistoryEntry<T>,
): Promise<BacktestHistorySummary> {
  if (!isHistoryEntry(entry)) throw new Error("Completed backtest result is invalid");
  const response = await fetch("/api/backtest-history", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(entry),
  });
  if (!response.ok) throw new Error(await responseDetail(response, "Backtest result could not be synced"));
  const payload = await response.json() as { run?: unknown };
  if (!isHistorySummary(payload.run)) throw new Error("Synced backtest summary is invalid");
  return payload.run;
}

export async function migrateBrowserBacktestHistory<T>(
  entries: BacktestHistoryEntry<T>[],
): Promise<BacktestHistorySummary[]> {
  for (const entry of entries.slice(0, BACKTEST_HISTORY_LIMIT)) {
    try {
      await saveAccountBacktestHistory(entry);
    } catch {
      // One malformed legacy browser entry must not prevent the other records from migrating.
    }
  }
  return readAccountBacktestHistory();
}

function newestFirst<T>(entries: BacktestHistoryEntry<T>[]): BacktestHistoryEntry<T>[] {
  return entries.sort((left, right) => {
    const timestampDifference = Date.parse(right.completedAt) - Date.parse(left.completedAt);
    return Number.isNaN(timestampDifference) || timestampDifference === 0
      ? right.id.localeCompare(left.id)
      : timestampDifference;
  });
}

async function readAll<T>(database: IDBDatabase): Promise<BacktestHistoryEntry<T>[]> {
  const transaction = database.transaction(STORE_NAME, "readonly");
  const request = transaction.objectStore(STORE_NAME).getAll();
  const values = await new Promise<unknown[]>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result as unknown[]);
    request.onerror = () => reject(request.error ?? new Error("Backtest history could not be read"));
  });
  await transactionComplete(transaction);
  return newestFirst(values.filter(isHistoryEntry) as BacktestHistoryEntry<T>[]);
}

export async function readBacktestHistory<T>(): Promise<BacktestHistoryEntry<T>[]> {
  const database = await openDatabase();
  try {
    return (await readAll<T>(database)).slice(0, BACKTEST_HISTORY_LIMIT);
  } finally {
    database.close();
  }
}

export async function saveBacktestHistory<T>(
  entry: BacktestHistoryEntry<T>,
): Promise<BacktestHistoryEntry<T>[]> {
  if (!isHistoryEntry(entry)) throw new Error("Completed backtest result is invalid");
  const database = await openDatabase();
  try {
    const existing = await readAll<T>(database);
    const retainedIds = new Set(
      existing
        .filter((item) => item.id !== entry.id)
        .slice(0, BACKTEST_HISTORY_LIMIT - 1)
        .map((item) => item.id),
    );
    const write = database.transaction(STORE_NAME, "readwrite");
    const store = write.objectStore(STORE_NAME);
    existing
      .filter((item) => item.id !== entry.id && !retainedIds.has(item.id))
      .forEach((item) => store.delete(item.id));
    store.put(entry);
    await transactionComplete(write);
    return (await readAll<T>(database)).slice(0, BACKTEST_HISTORY_LIMIT);
  } finally {
    database.close();
  }
}
