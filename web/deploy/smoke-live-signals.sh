#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
health_response="$(mktemp)"
signals_response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${health_response}" "${signals_response}"' EXIT

curl -fsS -o /dev/null -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login"

curl -fsS -b "${cookie_jar}" "${base_url}/signals?market=NSE" | grep -q 'Signals'
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/signals/health?market=NSE" > "${health_response}"
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/signals?market=NSE" > "${signals_response}"

jq -e '
  .paperOnly == true and
  .liveOrdersEnabled == false and
  (.engines | type == "array") and
  (.workers | type == "array")
' "${health_response}" >/dev/null

jq -e '
  .paperOnly == true and
  .liveOrdersEnabled == false and
  (.signals | type == "array") and
  (.colours | type == "object")
' "${signals_response}" >/dev/null

jq -n --slurpfile health "${health_response}" --slurpfile signals "${signals_response}" '{
  engines: $health[0].engines,
  workers: ($health[0].workers | length),
  signals: ($signals[0].signals | length),
  paperOnly: $signals[0].paperOnly
}'