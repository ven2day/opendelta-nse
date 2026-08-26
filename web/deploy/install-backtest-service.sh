#!/usr/bin/env bash
set -euo pipefail

release="/opt/vento-nse/current"
environment_file="/etc/vento-nse-dhan.env"

test -f "${environment_file}"
test -f "${release}/web/deploy/vento-nse-backtest.service"

if grep -Eq '^DHAN_TOKEN_CACHE_FILE=[^/]' "${environment_file}"; then
  echo "Dhan token cache path must be absolute" >&2
  exit 1
fi

install -d -o 10001 -g 10001 -m 0700 /var/lib/vento-nse/backtest
install -d -o 10001 -g 10001 -m 0700 /var/lib/vento-nse/dhan
install -d -m 0755 /var/lib/vento-nse/data
install -m 0644 \
  "${release}/web/deploy/vento-nse-backtest.service" \
  /etc/systemd/system/vento-nse-backtest.service

systemctl daemon-reload
systemctl enable --now vento-nse-backtest.service

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:3200/health >/dev/null; then
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:3200/health
systemctl --no-pager --full status vento-nse-backtest.service
