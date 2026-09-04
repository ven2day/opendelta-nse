#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:3200}"
response="$(mktemp)"
trap 'rm -f "${response}"' EXIT

# EMA/VWAP Strong Buy is the only strategy that can start a new backtest, and it
# requires completed 5-minute candles, so this smoke test always exercises it.
payload='{
  "symbols": ["LUPIN"],
  "durationYears": 1,
  "timeframe": "5m",
  "strategyMode": "ema_vwap_strong_buy",
  "strategyKey": "ema_vwap_strong_buy"
}'

curl -fsS \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/backtest" > "${response}"

jq -e '
  (.metadata.strategyMode == "ema_vwap_strong_buy") and
  (.results | length == 1) and
  (.results[0].symbol == "LUPIN") and
  (.results[0].bars > 200)
' "${response}" >/dev/null

jq -c '{
  symbol: .results[0].symbol,
  bars: .results[0].bars,
  strongBuySignals: .results[0].strongBuySignals,
  executedLots: .results[0].executedLots,
  targetHits: .results[0].targetHits,
  errors: .errors
}' "${response}"
