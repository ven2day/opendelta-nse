#!/usr/bin/env bash
set -euo pipefail

release="/opt/opendelta/current"
environment_file="/etc/opendelta-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/opendelta-backtest.service"

if grep -Eq '^DHAN_TOKEN_CACHE_FILE=[^/]' "${environment_file}"; then
  echo "Dhan token cache path must be absolute" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0700 /var/lib/opendelta/backtest
install -d -o 10001 -g 10001 -m 0700 /var/lib/opendelta/dhan
install -d -m 0755 /var/lib/opendelta/data
if [[ ! -s /var/lib/opendelta/data/symbols.csv ]]; then
  install -o 10001 -g 10001 -m 0644 "${release}/data/symbols.csv" /var/lib/opendelta/data/symbols.csv
fi
install -m 0644 \
  "${release}/web/deploy/opendelta-backtest.service" \
  /etc/systemd/system/opendelta-backtest.service

systemctl daemon-reload
systemctl enable --now opendelta-backtest.service

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:3200/health >/dev/null; then
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:3200/health
systemctl --no-pager --full status opendelta-backtest.service
