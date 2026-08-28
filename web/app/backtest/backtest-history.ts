export const BACKTEST_HISTORY_LIMIT = 10;

const DATABASE_NAME = "vento-nse-backtest-history";
const DATABASE_VERSION = 1;
const STORE_NAME = "completed-runs";

export type BacktestHistoryStrategy =
  | "rsi_range"
  | "rsi_recovery"
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
    && ["rsi_range", "rsi_recovery", "market_aligned_rsi_scalper"].includes(entry.strategyMode ?? "")
    && typeof entry.strategyName === "string"
    && typeof entry.timeframe === "string"
    && typeof entry.durationYears === "number"
    && typeof entry.symbolCount === "number"
    && Number.isInteger(entry.symbolCount)
    && entry.symbolCount > 0
    && Boolean(response && typeof response.metadata === "object" && Array.isArray(response.results));
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
