#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/opendelta.env
set +a

base_url="${1:-http://127.0.0.1:3100}"

cookie_jar="$(mktemp)"
dashboard_html="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${dashboard_html}"' EXIT

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
  for label in Dashboard Watchlist Backtest Signals 'Paper Trading' Settings; do
    grep -q "${label}" "${dashboard_html}"
  done
  grep -q 'OpenDelta' "${dashboard_html}"
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
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/dashboard?market=CRYPTO" > "${dashboard_html}"
jq -e '.market == "CRYPTO" and .paperOnly == true and .liveOrdersEnabled == false and (.marketData.data.market == "CRYPTO")' "${dashboard_html}" >/dev/null
curl -fsS -b "${cookie_jar}" "${base_url}/api/platform?action=market-context&market=CRYPTO" > "${dashboard_html}"
jq -e '.market == "CRYPTO" and .session.status == "OPEN_24_7" and .session.timezone == "UTC"' "${dashboard_html}" >/dev/null
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/strategies" > "${dashboard_html}"
jq -e '(.strategies | length) > 0 and (.strategies[0].configSchema | type == "object")' "${dashboard_html}" >/dev/null
echo "verified unified platform API proxy"

curl -fsS -b "${cookie_jar}" "${base_url}/" > "${dashboard_html}"
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

curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/signals/health?market=NSE" > "${dashboard_html}"
jq -e '
  .paperOnly == true and
  .liveOrdersEnabled == false and
  (.engines | type == "array") and
  (.workers | type == "object")
' "${dashboard_html}" >/dev/null
echo "verified paper-only v2 signal health contract"

universe_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  "${base_url}/api/v2/screener/universes?market=NSE")"
[[ "${universe_status}" == "401" ]]
echo "verified screener universes API authentication"

curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/screener/universes?market=NSE" > "${dashboard_html}"
jq -e '
  (.universes | type == "array") and
  (.active | type == "object")
' "${dashboard_html}" >/dev/null
echo "verified screener universes API proxy"

backtest_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data '{"market":"NSE","strategyId":"ema_vwap_strong_buy","symbols":[],"timeframe":"5m","startDate":"2026-01-01","endDate":"2026-01-02"}' \
  "${base_url}/api/v2/backtests")"
if [[ "${backtest_status}" != "422" ]]; then
  echo "backtest API verification returned HTTP ${backtest_status}" >&2
  head -c 500 "${dashboard_html}" >&2
  echo >&2
  exit 1
fi
grep -q 'detail' "${dashboard_html}"
echo "verified authenticated backtest API validation"

proxy_status="$(curl -sS -o "${dashboard_html}" -w '%{http_code}' \
  -H "x-opendelta-proxy-token: ${BACKTEST_PROXY_TOKEN}" \
  "${base_url}/api/v2/strategies")"
if [[ "${proxy_status}" != "200" ]]; then
  echo "trusted proxy verification returned HTTP ${proxy_status}" >&2
  head -c 500 "${dashboard_html}" >&2
  echo >&2
  exit 1
fi
jq -e '(.strategies | length) > 0' "${dashboard_html}" >/dev/null
echo "verified trusted v2 proxy"

echo "container verification passed"
