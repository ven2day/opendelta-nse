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

for page in / /screener /backtest /signals /paper-trading /settings; do
  curl -fsS -b "${cookie_jar}" "${base_url}${page}" > "${dashboard_html}"
  for label in Dashboard Screener Backtest Signals 'Paper Trading' Settings; do
    grep -q "${label}" "${dashboard_html}"
  done
  grep -q 'OpenDelta' "${dashboard_html}"
  ! grep -q 'Vento NSE' "${dashboard_html}"
  if [[ "${page}" != "/settings" ]]; then
    grep -q 'Market' "${dashboard_html}"
  fi
done
curl -fsS -b "${cookie_jar}" "${base_url}/paper-trading" > "${dashboard_html}"
grep -q 'Paper only' "${dashboard_html}"
echo "verified unified pages HTML"

v2_anonymous="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/api/v2/dashboard?market=NSE")"
[[ "${v2_anonymous}" == "401" ]]
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/dashboard?market=NSE" > "${dashboard_html}"
jq -e '.market == "NSE" and .paperOnly == true and .liveOrdersEnabled == false and (.marketData | type == "object")' "${dashboard_html}" >/dev/null
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/strategies" > "${dashboard_html}"
jq -e '(.strategies | length) > 0 and (.strategies[0].configSchema | type == "object")' "${dashboard_html}" >/dev/null
echo "verified unified platform API proxy"

curl -fsS -b "${cookie_jar}" "${base_url}/legacy/screener" > "${dashboard_html}"
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

mapfile -t browser_assets < <(
  grep -oE '(src|href)="[^"]+\.(js|css)(\?[^"]*)?"' "${dashboard_html}" \
    | sed -E 's/^(src|href)="([^"]+)"$/\2/' \
    | sort -u
)
[[ "${#browser_assets[@]}" -gt 0 ]]
for asset in "${browser_assets[@]}"; do
  if [[ "${asset}" == http* ]]; then
    asset_url="${asset}"
  else
    asset_url="${base_url}${asset}"
  fi
  curl -fsS -o /dev/null "${asset_url}"
done
echo "verified browser JavaScript and stylesheet assets"

curl -fsS -b "${cookie_jar}" "${base_url}/api/market-data?format=csv" > "${live_csv}"
grep -q '^rank,symbol,company_name,trading_date,previous_date,previous_close,entry_price,change_percent,previous_rsi_14,rsi_14,volume_24h,support_1_price,support_1_time,support_2_price,support_2_time,resistance_1_price,resistance_1_time,resistance_2_price,resistance_2_time' "${live_csv}"
registry_count="$(awk 'END { print NR - 1 }' /var/lib/vento-nse/data/symbols.csv)"
[[ "$(awk 'END { print NR - 1 }' "${live_csv}")" -eq "${registry_count}" ]]

session_count="$(awk -F, 'NR > 1 && $4 != "" { sessions[$4] = 1 } END { print length(sessions) }' "${live_csv}")"
[[ "${session_count}" -eq 1 ]]
echo "verified live market CSV"

curl -fsS -b "${cookie_jar}" "${base_url}/legacy/backtest" > "${dashboard_html}"
grep -q 'Historical backtest' "${dashboard_html}"
grep -q 'Run backtest' "${dashboard_html}"
grep -q 'Investment rules' "${dashboard_html}"
grep -Eq 'All .*[1-9][0-9]*.* symbols' "${dashboard_html}"
! grep -q 'Top-5 Opening Range Breakout' "${dashboard_html}"
! grep -q '>Market-Aligned VWAP Pullback Scalper</button>' "${dashboard_html}"
! grep -q '>Market-Aligned RSI Scalper</button>' "${dashboard_html}"
! grep -q 'Failure Engine' "${dashboard_html}"
grep -q '5m' "${dashboard_html}"
grep -q '4h' "${dashboard_html}"
grep -q '1d' "${dashboard_html}"
grep -q 'OpenDelta' "${dashboard_html}"
grep -q '₹' "${dashboard_html}"
! grep -q 'Vento NSE' "${dashboard_html}"
echo "verified backtest HTML"

curl -fsS -b "${cookie_jar}" "${base_url}/legacy/signals" > "${dashboard_html}"
grep -q 'OpenDelta' "${dashboard_html}"
! grep -q 'class="global-header"' "${dashboard_html}"
echo "verified legacy signals HTML"

curl -fsS -b "${cookie_jar}" "${base_url}/api/live-signals?action=status" > "${dashboard_html}"
jq -e '
  .paperOnly == true and
  .liveOrdersEnabled == false and
  .universeFrozen == true and
  (.universeVersion | type == "string" and length > 0) and
  (.monitoredSymbols > 0) and
  (.subscribedSymbols == .monitoredSymbols) and
  (
    (.marketSession == "CLOSED" and .engineStatus == "MARKET_CLOSED") or
    (
      .marketSession == "OPEN" and
      .connectionStatus == "CONNECTED" and
      (.engineStatus == "READY" or .engineStatus == "RECOVERING")
    )
  )
' "${dashboard_html}" >/dev/null
echo "verified paper-only NSE live-signal runtime contract"

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
