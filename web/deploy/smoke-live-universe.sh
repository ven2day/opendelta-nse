#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${response}"' EXIT

curl -fsS -o /dev/null -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login"

curl -fsS -b "${cookie_jar}" "${base_url}/signals?view=universe" | grep -q 'Live Signal Universe'

curl -fsS -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data '{
    "topN": 300,
    "minimumPrice": 500,
    "maximumPrice": 2000,
    "rankingMode": "QUALITY",
    "minimumBuyObservations": 50,
    "manualPins": [],
    "manualExclusions": [],
    "dynamicPriceFilter": false
  }' \
  "${base_url}/api/live-universe?action=rebuild" > "${response}"

jq -e '
  .status == "PREVIEW" and
  .configuration.topN == 300 and
  .configuration.minimumPrice == 500 and
  .configuration.maximumPrice == 2000 and
  .configuration.rankingMode == "QUALITY" and
  .statistics.totalNseSymbols == 750 and
  .statistics.dataQualityEligible == 749 and
  .statistics.selected <= 300 and
  .source.strategyVersion == "rsi-recovery-1.1.0" and
  .requiresConfirmation == true
' "${response}" >/dev/null

jq -c '{
  requested: .statistics.requestedTopN,
  priceEligible: .statistics.priceEligible,
  selected: .statistics.selected,
  priceAsOf: .source.priceAsOf,
  strategy: .source.strategyVersion,
  requiresConfirmation
}' "${response}"
