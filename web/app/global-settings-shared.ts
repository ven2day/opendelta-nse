export const GLOBAL_PRICE_MINIMUM = 0;
export const GLOBAL_PRICE_MAXIMUM = 10_000_000;

export type GlobalPriceRange = {
  minimumPrice: number;
  maximumPrice: number;
};

export type GlobalSettingsPayload = {
  schemaVersion: 1;
  priceRange: GlobalPriceRange;
  updatedAt: string | null;
};

export const DEFAULT_GLOBAL_PRICE_RANGE: GlobalPriceRange = {
  minimumPrice: GLOBAL_PRICE_MINIMUM,
  maximumPrice: GLOBAL_PRICE_MAXIMUM,
};

export function isPriceInGlobalRange(
  price: number | null | undefined,
  range: GlobalPriceRange,
): boolean {
  return typeof price === "number"
    && Number.isFinite(price)
    && price >= range.minimumPrice
    && price <= range.maximumPrice;
}

export function parseGlobalSettings(value: unknown): GlobalSettingsPayload {
  if (!value || typeof value !== "object") throw new Error("Global settings response is invalid");
  const payload = value as Partial<GlobalSettingsPayload>;
  const range = payload.priceRange;
  if (!range || !Number.isFinite(range.minimumPrice) || !Number.isFinite(range.maximumPrice)) {
    throw new Error("Global price range is invalid");
  }
  if (range.minimumPrice < GLOBAL_PRICE_MINIMUM || range.maximumPrice > GLOBAL_PRICE_MAXIMUM || range.minimumPrice >= range.maximumPrice) {
    throw new Error("Global price range is outside the supported limits");
  }
  return {
    schemaVersion: 1,
    priceRange: { minimumPrice: range.minimumPrice, maximumPrice: range.maximumPrice },
    updatedAt: typeof payload.updatedAt === "string" ? payload.updatedAt : null,
  };
}

export function formatGlobalPriceRange(range: GlobalPriceRange): string {
  const money = (value: number) => `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  if (range.minimumPrice === GLOBAL_PRICE_MINIMUM && range.maximumPrice === GLOBAL_PRICE_MAXIMUM) {
    return "All current prices";
  }
  return `${money(range.minimumPrice)}–${money(range.maximumPrice)}`;
}
