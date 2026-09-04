#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/opendelta.env
set +a

base_url="${1:-https://delta.ventoday.com}"
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

# Unified platform backtest: submit a v2 run for one NSE symbol over the last
# 90 days, then poll until the stored run reaches a terminal state.
start_date="$(date -u -d '90 days ago' +%F)"
end_date="$(date -u +%F)"

curl -fsS \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "{\"market\":\"NSE\",\"strategyId\":\"ema_vwap_strong_buy\",\"symbols\":[\"LUPIN\"],\"timeframe\":\"5m\",\"startDate\":\"${start_date}\",\"endDate\":\"${end_date}\"}" \
  "${base_url}/api/v2/backtests" > "${response}"

run_id="$(jq -r '.runId // empty' "${response}")"
[[ -n "${run_id}" ]]

status="QUEUED"
for _ in $(seq 1 120); do
  curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/backtests/${run_id}" > "${response}"
  status="$(jq -r '.status' "${response}")"
  case "${status}" in
    COMPLETE|FAILED|CANCELLED) break ;;
  esac
  sleep 5
done
[[ "${status}" == "COMPLETE" ]]

jq -e '
  .market == "NSE" and
  .strategyId == "ema_vwap_strong_buy" and
  (.symbolsProcessed >= 1) and
  (.metrics | type == "object")
' "${response}" >/dev/null

jq -c '{
  runId,
  status,
  symbolsProcessed,
  metrics
}' "${response}"