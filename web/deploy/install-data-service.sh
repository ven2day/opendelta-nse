#!/usr/bin/env bash
set -euo pipefail

release="/opt/opendelta/current"
environment_file="/etc/opendelta-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/opendelta-data.service"
test -f "${release}/web/deploy/opendelta-data.timer"

if grep -Eq '^DHAN_(TOKEN_CACHE_FILE|PREVIOUS_CLOSE_CACHE_FILE)=[^/]' "${environment_file}"; then
  echo "Dhan cache paths must be absolute" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0755 /var/lib/opendelta/data
install -d -o 10001 -g 10001 -m 0700 /var/lib/opendelta/dhan
if [[ ! -s /var/lib/opendelta/data/symbols.csv ]]; then
  install -o 10001 -g 10001 -m 0644 "${release}/data/symbols.csv" /var/lib/opendelta/data/symbols.csv
fi
install -m 0644 \
  "${release}/web/deploy/opendelta-data.service" \
  /etc/systemd/system/opendelta-data.service
install -m 0644 \
  "${release}/web/deploy/opendelta-data.timer" \
  /etc/systemd/system/opendelta-data.timer

systemctl daemon-reload
systemctl enable --now opendelta-data.timer
systemctl list-timers opendelta-data.timer --no-pager
