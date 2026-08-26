#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
archive="/tmp/vento-nse-deploy-${release_id}.tar.gz"
release="/opt/vento-nse/releases/${release_id}"

if [[ ! -d "${release}" ]]; then
  install -d -m 0755 "${release}"
  tar -xzf "${archive}" -C "${release}"
fi

if [[ ! -f /etc/vento-nse.env ]]; then
  umask 077
  app_password="$(openssl rand -base64 24 | tr -d '\n' | tr '/+' '_-')"
  auth_secret="$(openssl rand -hex 48)"
  printf 'APP_USERNAME=admin\nAPP_PASSWORD=%s\nAUTH_SECRET=%s\n' \
    "${app_password}" "${auth_secret}" > /etc/vento-nse.env
  printf '%s\n' "${app_password}" > /root/vento-nse-initial-password
fi

test -f "${release}/web/deploy/Dockerfile"
test -f "${release}/web/deploy/collector.Dockerfile"
test -f "${release}/web/deploy/backtest.Dockerfile"
test -s "${release}/nse_symbols_rsi_volume.csv"

docker build \
  --tag "vento-nse-dashboard:${release_id}" \
  --file "${release}/web/deploy/Dockerfile" \
  "${release}"

docker build \
  --tag "vento-nse-collector:${release_id}" \
  --tag "vento-nse-collector:current" \
  --file "${release}/web/deploy/collector.Dockerfile" \
  "${release}"

docker build \
  --tag "vento-nse-backtest:${release_id}" \
  --tag "vento-nse-backtest:current" \
  --file "${release}/web/deploy/backtest.Dockerfile" \
  "${release}"

ln -sfn "${release}" /opt/vento-nse/current
