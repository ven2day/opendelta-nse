#!/usr/bin/env bash
set -euo pipefail

release="/opt/vento-nse/current"
environment_file="/etc/vento-nse-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/vento-nse-data.service"
test -f "${release}/web/deploy/vento-nse-data.timer"

if grep -Eq '^DHAN_(TOKEN_CACHE_FILE|PREVIOUS_CLOSE_CACHE_FILE)=[^/]' "${environment_file}"; then
  echo "Dhan cache paths must be absolute" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0755 /var/lib/vento-nse/data
install -d -o 10001 -g 10001 -m 0700 /var/lib/vento-nse/dhan
install -m 0644 \
  "${release}/web/deploy/vento-nse-data.service" \
  /etc/systemd/system/vento-nse-data.service
install -m 0644 \
  "${release}/web/deploy/vento-nse-data.timer" \
  /etc/systemd/system/vento-nse-data.timer

systemctl daemon-reload
systemctl enable --now vento-nse-data.timer
systemctl list-timers vento-nse-data.timer --no-pager
