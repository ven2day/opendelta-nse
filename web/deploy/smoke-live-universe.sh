#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
cookie_jar="$(mktemp)"
universes_response="$(mktemp)"
presets_response="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${universes_response}" "${presets_response}"' EXIT

curl -fsS -o /dev/null -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login"

curl -fsS -b "${cookie_jar}" "${base_url}/screener?market=NSE" | grep -q 'Screener'
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/screener/universes?market=NSE" > "${universes_response}"
curl -fsS -b "${cookie_jar}" "${base_url}/api/v2/screener/presets?market=NSE" > "${presets_response}"

jq -e '
  (.universes | type == "array") and
  (.active | type == "object")
' "${universes_response}" >/dev/null

jq -e '
  (.presets | type == "array") and
  ((.presets | length) > 0) and
  (.presets[0].presetId | type == "string") and
  ((.presets[0].symbols | length) > 0)
' "${presets_response}" >/dev/null

jq -n \
  --slurpfile universes "${universes_response}" \
  --slurpfile presets "${presets_response}" \
  '{
    savedUniverses: ($universes[0].universes | length),
    presets: [$presets[0].presets[] | { presetId, symbolCount: (.symbols | length) }]
  }'