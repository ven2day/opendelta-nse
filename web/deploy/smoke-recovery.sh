#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:3200}"
timeframe="${2:-1d}"
response="$(mktemp)"
trap 'rm -f "${response}"' EXIT

payload="$(jq -nc --arg timeframe "${timeframe}" '{
  strategyMode: "rsi_recovery",
  universeMode: "selected",
  runId: "deployment-smoke",
  symbols: ["LUPIN"],
  durationYears: 1,
  timeframe: $timeframe,
  rsiLength: 14,
  rsiArmLow: 30,
  rsiArmHigh: 40,
  rsiRecovery: 40,
  emaEnabled: true,
  emaFast: 9,
  emaSlow: 20,
  vwapEnabled: true,
  volumeEnabled: true,
  volumeEma: 20,
  minimumConfirmations: 2,
  targetPct: 0.5,
  setupExpiryBars: 50,
  executionModel: "SIGNAL_CLOSE"
}')"

curl -fsS \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/backtest" > "${response}"

jq -e '
  .metadata.strategyMode == "rsi_recovery" and
  .metadata.executionModel == "SIGNAL_CLOSE" and
  .metadata.strategyParameters.minimumConfirmations == 2 and
  (.results | length) == 1 and
  .results[0].symbol == "LUPIN" and
  .results[0].bars > 200 and
  (.results[0].trades | type == "array")
' "${response}" >/dev/null

jq -c '{
  strategy: .metadata.strategyMode,
  symbol: .results[0].symbol,
  bars: .results[0].bars,
  buys: .summary.buySignals,
  targetsHit: .summary.targetsHit,
  hitRate: .summary.targetHitRate,
  open: .summary.stillOpen,
  errors: .errors
}' "${response}"
