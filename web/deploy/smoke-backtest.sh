#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1:3200}"
timeframe="${2:-1d}"
response="$(mktemp)"
trap 'rm -f "${response}"' EXIT

case "${timeframe}" in
  5m|15m|30m|1h|2h|4h|1d) ;;
  *) echo "Unsupported timeframe: ${timeframe}" >&2; exit 2 ;;
esac

payload="$(jq -nc --arg timeframe "${timeframe}" '{
  symbols: ["LUPIN"],
  durationYears: 1,
  timeframe: $timeframe
}')"

curl -fsS \
  -H 'Content-Type: application/json' \
  --data "${payload}" \
  "${base_url}/backtest" > "${response}"

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
