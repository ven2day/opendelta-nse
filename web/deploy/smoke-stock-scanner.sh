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

curl -fsS -b "${cookie_jar}" "${base_url}/signals/funnel" > "${page}"
grep -q 'NSE Signal Engine V2' "${page}"

anonymous_status="$(curl -sS -o /dev/null -w '%{http_code}' "${base_url}/api/stock-scanner")"
[[ "${anonymous_status}" == "401" ]]

curl --max-time 240 -fsS -b "${cookie_jar}" \
  "${base_url}/api/stock-scanner?refresh=true" > "${response}"

jq -e '
  .metadata.timeframe == "5m" and
  .metadata.rescanIntervalMinutes == 15 and
  .metadata.signalEvaluationIntervalMinutes == 5 and
  .metadata.paperOnly == true and
  .metadata.liveOrdersEnabled == false and
  .metadata.signalUniversePolicy == "SIGNAL_FIRST_FULL_ELIGIBLE_UNIVERSE" and
  (.metadata.symbolsRequested > 0) and
  (.metadata.symbolsLoaded > 0) and
  (.watchlist.topFive | length) == 5 and
  (.watchlist.primary | length) == 2 and
  (.watchlist.reserve | length) == 3 and
  (.opportunities | length) >= 5 and
  (.opportunities | length) <= 20 and
  .signalFunnel.metadata.paperOnly == true and
  .signalFunnel.metadata.liveOrdersEnabled == false and
  .signalFunnel.metadata.engineVersion == "nse-signal-engine-2.0.0" and
  .signalFunnel.metadata.rankingPolicy == "DISPLAY_ORDER_ONLY_NEVER_HIDES_SIGNALS" and
  .signalFunnel.metadata.configuration.maximumTradesPerDay == 5 and
  .signalFunnel.metadata.configuration.maximumConcurrent == 2 and
  (.signalFunnel.allSignals | length) == .signalFunnel.counts.validSetups and
  ([.signalFunnel.allSignals[] | select(.entryRange.minimum == null or .entryRange.maximum == null or .estimatedStop == null or .estimatedTarget == null)] | length) == 0 and
  ([.signalFunnel.allSignals[] | select((.whyBuy | length) == 0 or (.sellConditions | length) < 6)] | length) == 0 and
  ([.signalFunnel.allSignals[] | select(.historicalEvidence.status == "UNVALIDATED" and (.historicalEvidence.targetHitProbability != null or .historicalEvidence.expectedNetR != null))] | length) == 0 and
  ([.signalFunnel.metadata.strategies[] | select(.key == "nse_trend_pullback_continuation_v2")] | length) == 1 and
  ([.signalFunnel.metadata.strategies[] | select(.key == "nse_breakout_retest_v2")] | length) == 1
' "${response}" >/dev/null

jq -c '{
  status: .metadata.status,
  session: .metadata.sessionDate,
  rescan: .metadata.lastRescanTimestamp,
  requested: .metadata.symbolsRequested,
  loaded: .metadata.symbolsLoaded,
  scored: .metadata.symbolsScored,
  topFive: [.watchlist.topFive[] | {rank: .rankAfter, symbol, tier, score}],
  validSignals: [.signalFunnel.allSignals[] | {rank, symbol, strategyKey, status, entryRange, estimatedStop, estimatedTarget, historicalEvidence}],
  rejectionCounts: .signalFunnel.rejectionCounts,
  liveOrdersEnabled: .metadata.liveOrdersEnabled
}' "${response}"
