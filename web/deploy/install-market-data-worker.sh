#!/usr/bin/env bash
set -euo pipefail

release="/opt/opendelta/current"
environment_file="/etc/opendelta-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/opendelta-market-data-worker.service"
test -f "${release}/web/deploy/opendelta-market-data-worker.timer"
grep -Eq '^MARKET_DATA_DATABASE_URL=postgres(ql)?://' "${environment_file}"

install -d -o 10001 -g 10001 -m 0700 /var/lib/opendelta/dhan

install -m 0644 \
  "${release}/web/deploy/opendelta-market-data-worker.service" \
  /etc/systemd/system/opendelta-market-data-worker.service
install -m 0644 \
  "${release}/web/deploy/opendelta-market-data-worker.timer" \
  /etc/systemd/system/opendelta-market-data-worker.timer

systemctl daemon-reload
systemctl enable --now opendelta-market-data-worker.timer
systemctl list-timers opendelta-market-data-worker.timer --no-pager
