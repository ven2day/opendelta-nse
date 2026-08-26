#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
status_response="$(mktemp)"
settings_response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${status_response}" "${settings_response}"' EXIT

curl -fsS -o /dev/null -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login"

curl -fsS -b "${cookie_jar}" "${base_url}/signals" | grep -q 'Signals'
curl -fsS -b "${cookie_jar}" "${base_url}/api/live-signals?action=status" > "${status_response}"
curl -fsS -b "${cookie_jar}" "${base_url}/api/live-signals?action=settings" > "${settings_response}"

jq -e '
  .universeVersion == "LIVE-20260826-001" and
  .universeFrozen == true and
  .monitoredSymbols == 300 and
  .timeframe == "5m" and
  .strategyVersion == "rsi-recovery-1.1.0" and
  .paperOnly == true and
  .liveOrdersEnabled == false
' "${status_response}" >/dev/null

jq -e '
  .settings.entryRangeMethod == "FIXED_PERCENT" and
  .settings.fixedLowerPct == 0.15 and
  .settings.fixedUpperPct == 0.10 and
  .settings.paperAllocation == 25000 and
  .strategy.targetPct == 0.5 and
  .strategy.minimumConfirmations == 2 and
  .strategy.brokerExecution == false
' "${settings_response}" >/dev/null

jq -n --slurpfile status "${status_response}" --slurpfile settings "${settings_response}" '{
  universe: $status[0].universeVersion,
  monitored: $status[0].monitoredSymbols,
  subscribed: $status[0].subscribedSymbols,
  connection: $status[0].connectionStatus,
  engine: $status[0].engineStatus,
  lastCompletedCandle: $status[0].lastCompletedCandle,
  paperOnly: $status[0].paperOnly,
  rangeMethod: $settings[0].settings.entryRangeMethod,
  allocation: $settings[0].settings.paperAllocation
}'
