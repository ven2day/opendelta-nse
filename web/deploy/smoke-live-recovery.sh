#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
timeframe="${2:-1d}"
cookie_jar="$(mktemp)"
response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${response}"' EXIT

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]

payload="$(jq -nc --arg timeframe "${timeframe}" '{
  strategyMode: "rsi_recovery",
  universeMode: "selected",
  runId: "live-deployment-smoke",
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
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/api/backtest" > "${response}"

jq -e '
  .metadata.strategyMode == "rsi_recovery" and
  .metadata.executionModel == "SIGNAL_CLOSE" and
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
