#!/usr/bin/env bash
set -euo pipefail

set -a
source /etc/vento-nse.env
set +a

base_url="${1:-https://nse.ventoday.com}"
exporter_container="${2:-vento-nse}"
cookie_jar="$(mktemp)"
response="$(mktemp)"
page="$(mktemp)"
symbols_json="$(mktemp)"
markdown="$(mktemp)"
trap 'rm -f "${cookie_jar}" "${response}" "${page}" "${symbols_json}" "${markdown}"' EXIT

login_status="$(curl -sS -o /dev/null -w '%{http_code}' \
  -c "${cookie_jar}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${APP_USERNAME}" \
  --data-urlencode "password=${APP_PASSWORD}" \
  "${base_url}/api/login")"
[[ "${login_status}" == "303" ]]

curl -fsS -b "${cookie_jar}" "${base_url}/backtest" > "${page}"
grep -q 'RSI Range Strategy' "${page}"
grep -q 'RSI Recovery Scalping' "${page}"
grep -q 'Top-5 Opening Range Breakout' "${page}"
! grep -q '>Market-Aligned VWAP Pullback Scalper</button>' "${page}"

awk -F, 'NR > 1 && $1 != "" { print $1 } NR == 21 { exit }' \
  /var/lib/vento-nse/data/symbols.csv \
  | jq -Rsc 'split("\n") | map(select(length > 0))' > "${symbols_json}"
[[ "$(jq 'length' "${symbols_json}")" -ge 5 ]]

payload="$(jq -nc --slurpfile symbols "${symbols_json}" '{
  strategyMode: "top_5_opening_range_breakout",
  strategyKey: "top_5_opening_range_breakout",
  universeMode: "selected",
  runId: "production-top-5-opening-range-breakout-smoke",
  cachePolicy: "RUN_AGAIN",
  symbols: $symbols[0],
  durationYears: 1,
  timeframe: "5m",
  top5OpeningRangeBreakoutConfiguration: {
    watchlistMode: "FROZEN_OPEN",
    quantityPerTrade: 50
  }
}')"

curl -fsS \
  -b "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/api/backtest?action=start-job" > "${response}"
job_id="$(jq -r '.jobId // empty' "${response}")"
[[ -n "${job_id}" ]]

for _ in {1..360}; do
  curl -fsS -b "${cookie_jar}" \
    "${base_url}/api/backtest/jobs/${job_id}" > "${response}"
  status="$(jq -r '.status' "${response}")"
  case "${status}" in
    COMPLETE) break ;;
    FAILED|CANCELLED) jq . "${response}" >&2; exit 1 ;;
  esac
  sleep 5
done
[[ "$(jq -r '.status' "${response}")" == "COMPLETE" ]]

jq '.result' "${response}" > "${page}"
jq -e '
  .metadata.strategyKey == "top_5_opening_range_breakout" and
  .metadata.strategyName == "Top-5 Opening Range Breakout" and
  .metadata.watchlistMode == "FROZEN_OPEN" and
  .metadata.effectiveConfiguration.watchlistMode == "FROZEN_OPEN" and
  .metadata.effectiveConfiguration.quantityPerTrade == 50 and
  (.metadata.universeEvaluated >= 5) and
  (.metadata.tradingDays >= 1) and
  (any(.dailySelections[]; (.symbols | length) == 5)) and
  (all(.trades[]; .executedQuantity == 50))
' "${page}" >/dev/null
! grep -qi 'VWAP pullback performance' "${page}"

docker exec -i "${exporter_container}" node --input-type=module -e '
  const { buildTop5OpeningRangeBreakoutMarkdown } = await import(
    "file:///app/web/app/backtest/top-5-opening-range-breakout-contract.mjs"
  );
  let source = "";
  for await (const chunk of process.stdin) source += chunk;
  process.stdout.write(buildTop5OpeningRangeBreakoutMarkdown(JSON.parse(source)));
' < "${page}" > "${markdown}"

grep -q '^# Top-5 Opening Range Breakout$' "${markdown}"
grep -q '^- Strategy: Top-5 Opening Range Breakout$' "${markdown}"
grep -q '^- Watchlist mode: FROZEN_OPEN$' "${markdown}"
grep -q '^## Daily watchlists$' "${markdown}"
grep -q '^## Effective settings$' "${markdown}"
grep -q '09:30:00+05:30' "${markdown}"
! grep -qi 'VWAP pullback performance' "${markdown}"

jq -c '{
  strategy: .metadata.strategyKey,
  watchlistMode: .metadata.watchlistMode,
  universeEvaluated: .metadata.universeEvaluated,
  tradingDays: .metadata.tradingDays,
  firstDailyWatchlist: (.dailySelections | map(select((.symbols | length) == 5)) | first),
  acceptedSignals: .summary.acceptedBuySignals,
  executedTrades: .summary.executedTrades,
  configurationHash: .metadata.configurationHash,
  source: .metadata.resultSource
}' "${page}"

echo "Top-5 Opening Range Breakout authenticated production smoke passed"
