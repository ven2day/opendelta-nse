#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
page="$(mktemp)"
response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${page}" "${response}"' EXIT
research_payload='{"researchVersion":"2","mode":"EXACT","market":"NSE","provider":"DHAN","baseStrategyId":"neutral_research_trigger","symbols":["LUPIN"],"startDate":"2026-01-01","endDate":"2026-02-01","contextTimeframe":"15m","setupTimeframe":"5m","executionTimeframe":"1m","direction":"LONG","factorSelections":["ema_alignment"],"factorParameters":{},"minimumTrades":5,"beamWidth":1}'

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]

anonymous_status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/api/platform?action=overview")"
[[ "${anonymous_status}" == "401" ]]

declare -A pages=(
  ["/markets"]="NSE market research"
  ["/research"]="Learn the factor catalogue"
  ["/research/experiments"]="Design a bounded experiment"
  ["/research/results"]="Experiment results"
  ["/strategies"]="Versioned strategy catalog"
  ["/risk"]="Research risk controls"
  ["/data-health"]="Data Health"
  ["/jobs"]="Background jobs"
  ["/settings"]="Platform settings"
)

for route in "${!pages[@]}"; do
  curl -fsS -b "${cookie_jar}" "${base_url}${route}" > "${page}"
  grep -q "${pages[${route}]}" "${page}"
  grep -q 'OpenDelta' "${page}"
  ! grep -q 'Internal Server Error' "${page}"
done

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=overview" > "${response}"
jq -e '
  .platform == "OpenDelta" and
  .paperOnly == true and
  .liveOrdersEnabled == false and
  (.factorCount >= 20) and
  (.factorFamilies | length) == 10 and
  (.strategyCount >= 6) and
  .researchEngine.version == "2" and
  .researchEngine.enabled == false and
  .researchEngine.status == "DISABLED_FAIL_CLOSED" and
  .researchEngine.legacyResultStatus == "LEGACY_INVALID_RESEARCH_MODEL"
' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=factors" > "${response}"
jq -e '
  (.count >= 20) and
  ([.rows[] | select(.factor_id == "adx" and (.measures | ascii_downcase | contains("never direction")))] | length) == 1 and
  ([.rows[] | select(.factor_id == "historical_spread" and .missing_data_behavior == "UNSUPPORTED_DATA_REQUIREMENT")] | length) == 1 and
  ([.rows[] | select(.factor_id == "relative_nifty" and (.misunderstanding | contains("not RSI")))] | length) == 1
' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=strategies&market=NSE" > "${response}"
jq -e '
  .paperOnly == true and .liveOrdersEnabled == false and
  ([.rows[] | select(.key == "rsi_recovery" and .status == "ACTIVE")] | length) == 1 and
  ([.rows[] | select(.key == "market_aligned_vwap_pullback_scalper" and .status == "RETIRED")] | length) == 1 and
  ([.rows[] | select(.live_orders_enabled == true)] | length) == 0
' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=risk" > "${response}"
jq -e '.paperOnly == true and .liveOrdersEnabled == false and .policy.maximum_open_positions == 2' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=data-health" > "${response}"
jq -e '
  (.providers | length) == 3 and
  ([.providers[] | select(.privateTradingEndpoints != false)] | length) == 0 and
  ([.providers[] | select(.provider == "OKX")] | length) == 1 and
  ([.providers[] | select(.provider == "VALR")] | length) == 1
' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=market-context&market=NSE" > "${response}"
jq -e '
  .market == "NSE" and
  (.session.timezone == "Asia/Kolkata") and
  (.benchmarkDirection.status == "UNSUPPORTED_DATA_REQUIREMENT") and
  (.sectorDirection.status == "UNSUPPORTED_DATA_REQUIREMENT")
' "${response}" >/dev/null

curl -fsS -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${research_payload}" \
  "${base_url}/api/platform?action=estimate" > "${response}"
jq -e '.possibleCombinations == 1 and .plannedBacktests == 1 and .bounded == true' "${response}" >/dev/null

research_status="$(curl -sS -o "${response}" -w '%{http_code}' -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${research_payload}" \
  "${base_url}/api/platform?action=experiment")"
[[ "${research_status}" == "503" ]]
jq -e '.detail | contains("RESEARCH_ENGINE_V2_DISABLED")' "${response}" >/dev/null

echo "quant platform smoke passed"
