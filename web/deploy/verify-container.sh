#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-http://127.0.0.1:3100}"

cookie_jar="$(mktemp)"
dashboard_html="$(mktemp)"
live_csv="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${dashboard_html}" "${live_csv}"' EXIT

anonymous_status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/")"
[[ "${anonymous_status}" == "307" ]]
echo "verified anonymous redirect"

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]
echo "verified login"

curl -fsS -b "${cookie_jar}" "${base_url}/" > "${dashboard_html}"
grep -q 'Yesterday RSI' "${dashboard_html}"
grep -q 'Current RSI' "${dashboard_html}"
grep -q 'Yesterday price' "${dashboard_html}"
grep -q 'Current close' "${dashboard_html}"
grep -q 'Change (₹)' "${dashboard_html}"
grep -q 'Recent levels' "${dashboard_html}"
grep -q 'Confirmed 1-day pivots' "${dashboard_html}"
! grep -q 'Awaiting confirmed daily pivots' "${dashboard_html}"
grep -q '24h volume' "${dashboard_html}"
grep -q 'RSI &gt; 50' "${dashboard_html}"
grep -q 'RSI slicer' "${dashboard_html}"
grep -q 'Minimum current RSI' "${dashboard_html}"
grep -q 'Maximum current RSI' "${dashboard_html}"
grep -q 'Price slicer' "${dashboard_html}"
grep -q 'Minimum current price' "${dashboard_html}"
grep -q 'Maximum current price' "${dashboard_html}"
grep -q 'Dhan market data' "${dashboard_html}"
grep -q 'Refresh all NSE data from Dhan' "${dashboard_html}"
grep -q 'Add NSE symbol' "${dashboard_html}"
! grep -q 'Export CSV' "${dashboard_html}"
grep -Eq '[0-9]{2} [A-Z][a-z]{2} [0-9]{2}:[0-9]{2} (AM|PM)' "${dashboard_html}"
! grep -q 'Last refresh' "${dashboard_html}"
! grep -q 'NSE ready' "${dashboard_html}"
! grep -q 'All prices' "${dashboard_html}"
! grep -q 'Extra large' "${dashboard_html}"
grep -q 'IST' "${dashboard_html}"
grep -q 'Backtest' "${dashboard_html}"
grep -q 'OpenDelta' "${dashboard_html}"
grep -q '₹' "${dashboard_html}"
! grep -q 'Vento NSE' "${dashboard_html}"
! grep -q '>2h volume<' "${dashboard_html}"
! grep -q '>4h volume<' "${dashboard_html}"
echo "verified dashboard HTML"

curl -fsS -b "${cookie_jar}" "${base_url}/api/market-data?format=csv" > "${live_csv}"
grep -q '^rank,symbol,company_name,trading_date,previous_date,previous_close,entry_price,change_percent,previous_rsi_14,rsi_14,volume_24h,support_1_price,support_1_time,support_2_price,support_2_time,resistance_1_price,resistance_1_time,resistance_2_price,resistance_2_time' "${live_csv}"
registry_count="$(awk 'END { print NR - 1 }' /var/lib/vento-nse/data/symbols.csv)"
[[ "$(awk 'END { print NR - 1 }' "${live_csv}")" -eq "${registry_count}" ]]

session_count="$(awk -F, 'NR > 1 && $4 != "" { sessions[$4] = 1 } END { print length(sessions) }' "${live_csv}")"
[[ "${session_count}" -eq 1 ]]
echo "verified live market CSV"

curl -fsS -b "${cookie_jar}" "${base_url}/backtest" > "${dashboard_html}"
grep -q 'Historical backtest' "${dashboard_html}"
grep -q 'Run backtest' "${dashboard_html}"
grep -q 'Investment rules' "${dashboard_html}"
grep -Eq 'All .*[1-9][0-9]*.* symbols' "${dashboard_html}"
grep -q 'RSI Range Strategy' "${dashboard_html}"
grep -q 'RSI Recovery Scalping' "${dashboard_html}"
grep -q 'Top-5 Opening Range Breakout' "${dashboard_html}"
! grep -q '>Market-Aligned VWAP Pullback Scalper</button>' "${dashboard_html}"
! grep -q '>Market-Aligned RSI Scalper</button>' "${dashboard_html}"
grep -q 'at least 1% net profit after fees' "${dashboard_html}"
grep -q 'wait for a later high-RSI opportunity' "${dashboard_html}"
grep -q '5m' "${dashboard_html}"
grep -q '4h' "${dashboard_html}"
grep -q '1d' "${dashboard_html}"
grep -q 'OpenDelta' "${dashboard_html}"
grep -q '₹' "${dashboard_html}"
! grep -q 'Vento NSE' "${dashboard_html}"
echo "verified backtest HTML"

curl -fsS -b "${cookie_jar}" "${base_url}/signals" > "${dashboard_html}"
grep -q 'Completed-candle research monitor' "${dashboard_html}"
grep -q 'Paper positions' "${dashboard_html}"
echo "verified signals HTML"

curl -fsS -b "${cookie_jar}" "${base_url}/scanner" > "${dashboard_html}"
grep -q 'Stock Scanner' "${dashboard_html}"
grep -q 'Top 20 opportunities' "${dashboard_html}"
grep -q 'paper research' "${dashboard_html}"
echo "verified stock scanner HTML"

scanner_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  "${base_url}/api/stock-scanner")"
[[ "${scanner_status}" == "401" ]]
echo "verified stock scanner API authentication"

universe_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  "${base_url}/api/live-universe?action=config")"
[[ "${universe_status}" == "401" ]]
echo "verified live-universe API authentication"

market_data_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  "${base_url}/api/market-data")"
[[ "${market_data_status}" == "401" ]]

curl -fsS -b "${cookie_jar}" "${base_url}/api/market-data" > "${dashboard_html}"
jq -e '
  (.state == "IDLE" or .state == "RUNNING" or .state == "SUCCEEDED" or .state == "FAILED") and
  (.running | type == "boolean") and
  (.lastRefreshTimestamp | type == "string")
' "${dashboard_html}" >/dev/null
echo "verified authenticated market-data status proxy"

curl -fsS \
  -H "x-opendelta-proxy-token: ${BACKTEST_PROXY_TOKEN}" \
  "${base_url}/api/live-universe?action=config" > "${dashboard_html}"
jq -e '
  .defaults.topN == 300 and
  .defaults.minimumPrice == 500 and
  .defaults.maximumPrice == 2000 and
  .defaults.rankingMode == "QUALITY" and
  .defaults.minimumBuyObservations == 50
' "${dashboard_html}" >/dev/null
echo "verified live-universe API proxy"

backtest_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data '{"symbols":["NOTINUNIVERSE"]}' \
  "${base_url}/api/backtest")"
if [[ "${backtest_status}" != "422" ]]; then
  echo "backtest API verification returned HTTP ${backtest_status}" >&2
  head -c 500 "${dashboard_html}" >&2
  echo >&2
  exit 1
fi
grep -q 'symbols.csv' "${dashboard_html}"
echo "verified authenticated backtest API proxy"

proxy_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -H "x-opendelta-proxy-token: ${BACKTEST_PROXY_TOKEN}" \
  --data '{"symbols":[]}' \
  "${base_url}/api/backtest")"
if [[ "${proxy_status}" != "422" ]]; then
  echo "trusted backtest proxy verification returned HTTP ${proxy_status}" >&2
  head -c 500 "${dashboard_html}" >&2
  echo >&2
  exit 1
fi
grep -q 'detail' "${dashboard_html}"
echo "verified trusted backtest proxy"

echo "container verification passed"
