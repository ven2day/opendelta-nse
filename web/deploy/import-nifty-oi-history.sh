#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 FROM_DATE TO_DATE" >&2
  exit 2
fi

from_date="$1"
to_date="$2"
environment_file="${DHAN_ENV_FILE:-/etc/vento-nse-dhan.env}"
expiry_schedule_file="${NIFTY_EXPIRY_SCHEDULE_FILE:-/etc/vento-nse-nifty-expiry-schedule.json}"
backtest_root="${BACKTEST_DATA_ROOT:-/var/lib/vento-nse/backtest}"
dhan_root="${DHAN_DATA_ROOT:-/var/lib/vento-nse/dhan}"
image="${NIFTY_OI_IMPORT_IMAGE:-vento-nse-backtest:current}"
settings_file="${backtest_root}/live-signals/settings.json"

test -f "${environment_file}"
test -r "${expiry_schedule_file}"
docker image inspect "${image}" >/dev/null

if [[ -s "${settings_file}" ]] && grep -Eq '"oiFilterMode"[[:space:]]*:[[:space:]]*"(ADVISORY|ENFORCED)"' "${settings_file}"; then
  echo "Set the live NIFTY OI filter to OFF before importing historical observations" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0700 "${backtest_root}" "${dhan_root}"

flock -n "${backtest_root}/.nifty-oi-history-import.lock" \
  docker run --rm \
    --name vento-nse-oi-history-import \
    --env-file "${environment_file}" \
    --mount "type=bind,source=${backtest_root},target=/var/lib/vento-nse/backtest" \
    --mount "type=bind,source=${dhan_root},target=/var/lib/vento-nse/dhan" \
    --mount "type=bind,source=${expiry_schedule_file},target=/run/nifty-expiry-schedule.json,readonly" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=128m \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --pids-limit 256 \
    --memory 4g \
    "${image}" \
    python import_nifty_oi_history.py \
      --from-date "${from_date}" \
      --to-date "${to_date}" \
      --strikes-each-side 5 \
      --output /var/lib/vento-nse/backtest/nifty-oi \
      --cache /var/lib/vento-nse/dhan/nifty-oi-history-cache \
      --expiry-schedule /run/nifty-expiry-schedule.json
