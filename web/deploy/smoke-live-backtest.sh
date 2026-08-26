#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
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

curl -fsS \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data '{"symbols":["LUPIN"],"durationYears":1,"timeframe":"1d"}' \
  "${base_url}/api/backtest" > "${response}"

jq -e '
  .results | length == 1 and
  .[0].symbol == "LUPIN" and
  .[0].bars > 200 and
  (.[0].niftyReturnPct | type == "number")
' "${response}" >/dev/null

jq -c '{
  symbol: .results[0].symbol,
  bars: .results[0].bars,
  trades: .results[0].closedTrades,
  returnPct: .results[0].strategyReturnPct,
  niftyReturnPct: .results[0].niftyReturnPct,
  errors: .errors
}' "${response}"
