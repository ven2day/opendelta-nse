#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 SESSION_CALENDAR.csv START_ISO END_ISO [--enqueue]" >&2
  exit 2
}

[[ "$#" -eq 3 || "$#" -eq 4 ]] || usage
calendar_file="$(realpath -- "$1")"
metadata_file="${calendar_file}.metadata.json"
start="$2"
end="$3"
enqueue="${4:-}"
[[ -z "${enqueue}" || "${enqueue}" == "--enqueue" ]] || usage
test -s "${calendar_file}"
test -s "${metadata_file}"
test -f /etc/vento-nse-dhan.env
docker inspect --format '{{.State.Health.Status}}' vento-nse-timescale | grep -qx healthy

docker run --rm \
  --mount type=bind,source="${metadata_file}",target=/run/nse-sessions.metadata.json,readonly \
  --mount type=bind,source="${calendar_file}",target=/run/nse-sessions.csv,readonly \
  vento-nse-backtest:current \
  python -c 'import hashlib,json,sys; from datetime import datetime; m=json.load(open("/run/nse-sessions.metadata.json")); start=datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")).date(); end=datetime.fromisoformat(sys.argv[2].replace("Z","+00:00")).date(); actual=hashlib.sha256(open("/run/nse-sessions.csv","rb").read()).hexdigest(); assert m["market"] == "NSE"; assert m["validFrom"] <= start.isoformat(); assert m["validThrough"] >= end.isoformat(); assert m["calendarRowCount"] >= m["tradingDayCount"] > 0; assert m["calendarSha256"] == actual' \
  "${start}" "${end}"

run_admin() {
  docker run --rm \
    --network vento-nse-internal \
    --env-file /etc/vento-nse-dhan.env \
    --mount type=bind,source="${calendar_file}",target=/run/nse-sessions.csv,readonly \
    --mount type=bind,source=/var/lib/vento-nse/backtest,target=/var/lib/vento-nse/backtest \
    vento-nse-backtest:current \
    python -m backend.data.admin "$@"
}

run_admin migrate
run_admin load-sessions --market NSE --file /run/nse-sessions.csv
run_admin health

if [[ "${enqueue}" == "--enqueue" ]]; then
  run_admin enqueue-nse-universe --timeframe 5m --start "${start}" --end "${end}"
  run_admin enqueue-okx-configured --timeframe 5m --start "${start}" --end "${end}"
  echo "Backfills queued. Enable the worker timer only after reviewing the job counts above."
else
  echo "Calendar loaded without queueing backfills. Re-run with --enqueue after review."
fi
