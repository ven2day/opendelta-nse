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

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]

curl -fsS -b "${cookie_jar}" "${base_url}/scanner" > "${page}"
grep -q 'Stock Scanner' "${page}"
grep -q 'paper research' "${page}"

anonymous_status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/api/stock-scanner")"
[[ "${anonymous_status}" == "401" ]]

curl --max-time 240 -fsS -b "${cookie_jar}" \
  "${base_url}/api/stock-scanner?refresh=true" > "${response}"

jq -e '
  .metadata.timeframe == "5m" and
  .metadata.rescanIntervalMinutes == 15 and
  .metadata.paperOnly == true and
  .metadata.liveOrdersEnabled == false and
  .metadata.signalUniversePolicy == "FROZEN_AT_09_30" and
  (.metadata.symbolsRequested > 0) and
  (.metadata.symbolsLoaded > 0) and
  (.watchlist.topFive | length) == 5 and
  (.watchlist.primary | length) == 2 and
  (.watchlist.reserve | length) == 3 and
  (.opportunities | length) >= 5 and
  (.opportunities | length) <= 20
' "${response}" >/dev/null

jq -c '{
  status: .metadata.status,
  session: .metadata.sessionDate,
  rescan: .metadata.lastRescanTimestamp,
  requested: .metadata.symbolsRequested,
  loaded: .metadata.symbolsLoaded,
  scored: .metadata.symbolsScored,
  topFive: [.watchlist.topFive[] | {rank: .rankAfter, symbol, tier, score}],
  liveOrdersEnabled: .metadata.liveOrdersEnabled
}' "${response}"
