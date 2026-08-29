#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
response="$(mktemp)"
page="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${response}" "${page}"' EXIT

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]

curl -fsS -b "${cookie_jar}" "${base_url}/backtest" > "${page}"
grep -q 'RSI Range Strategy' "${page}"
grep -q 'RSI Recovery Scalping' "${page}"
grep -q 'Market-Aligned VWAP Pullback Scalper' "${page}"
! grep -q '>Market-Aligned RSI Scalper</button>' "${page}"
grep -q 'JSON configuration' "${page}"

curl -fsS -b "${cookie_jar}" "${base_url}/signals" > "${page}"
grep -q 'Completed-candle research monitor' "${page}"

symbol="$(awk -F, 'NR == 2 { print $1; exit }' /var/lib/vento-nse/data/symbols.csv)"
[[ -n "${symbol}" ]]
payload="$(jq -nc --arg symbol "${symbol}" '{
  strategyMode: "market_aligned_vwap_pullback_scalper",
  universeMode: "selected",
  runId: "production-vwap-pullback-smoke",
  cachePolicy: "RUN_AGAIN",
  symbols: [$symbol],
  durationYears: 1,
  timeframe: "5m"
}')"

curl -fsS \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/api/backtest" > "${response}"

jq -e '
  .metadata.strategyKey == "market_aligned_vwap_pullback_scalper" and
  .metadata.strategyName == "Market-Aligned VWAP Pullback Scalper" and
  .metadata.configuration.executionModel == "NEXT_BAR_OPEN" and
  .metadata.configuration.oiMode == "OFF" and
  .metadata.researchLabel == "Research candidate — paper trading required" and
  (.summary.rawCandidates | type == "number") and
  (.summary.acceptedBuySignals | type == "number") and
  (.summary.executedTrades | type == "number")
' "${response}" >/dev/null
jq -c '{
  strategy: .metadata.strategyKey,
  source: .metadata.resultSource,
  rawCandidates: .summary.rawCandidates,
  acceptedSignals: .summary.acceptedBuySignals,
  executedTrades: .summary.executedTrades,
  netPnl: .summary.netPnl,
  configurationHash: .metadata.configurationHash
}' "${response}"

retired_payload="$(jq -nc --arg symbol "${symbol}" '{
  strategyMode: "market_aligned_rsi_scalper",
  symbols: [$symbol],
  durationYears: 1,
  timeframe: "5m"
}')"
retired_status="$(curl -sS -o "${response}" -w '%{http_code}' \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${retired_payload}" \
  "${base_url}/api/backtest")"
[[ "${retired_status}" == "422" ]]

echo "VWAP pullback deployment smoke passed"
