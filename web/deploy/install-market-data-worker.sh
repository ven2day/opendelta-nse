#!/usr/bin/env bash
set -euo pipefail

release="/opt/vento-nse/current"
environment_file="/etc/vento-nse-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/vento-nse-market-data-worker.service"
test -f "${release}/web/deploy/vento-nse-market-data-worker.timer"
grep -Eq '^MARKET_DATA_DATABASE_URL=postgres(ql)?://' "${environment_file}"

install -d -o 10001 -g 10001 -m 0700 /var/lib/vento-nse/dhan

install -m 0644 \
  "${release}/web/deploy/vento-nse-market-data-worker.service" \
  /etc/systemd/system/vento-nse-market-data-worker.service
install -m 0644 \
  "${release}/web/deploy/vento-nse-market-data-worker.timer" \
  /etc/systemd/system/vento-nse-market-data-worker.timer

systemctl daemon-reload
systemctl enable --now vento-nse-market-data-worker.timer
systemctl list-timers vento-nse-market-data-worker.timer --no-pager
