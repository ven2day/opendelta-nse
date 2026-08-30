#!/usr/bin/env bash
set -euo pipefail

release="/opt/vento-nse/current"
database_environment="/etc/vento-nse-timescale.env"
application_environment="/etc/vento-nse-dhan.env"
database_user="opendelta"
database_name="opendelta"

test "$(id -u)" -eq 0
test -f "${application_environment}"
for asset in \
  vento-nse-timescale.service \
  vento-nse-timescale-backup.service \
  vento-nse-timescale-backup.timer \
  backup-timescale.sh \
  restore-timescale.sh; do
  test -f "${release}/web/deploy/${asset}"
done
command -v docker >/dev/null
command -v openssl >/dev/null

if [[ ! -f "${database_environment}" ]]; then
  umask 077
  database_password="$(openssl rand -hex 32)"
  {
    echo "POSTGRES_USER=${database_user}"
    echo "POSTGRES_DB=${database_name}"
    echo "POSTGRES_PASSWORD=${database_password}"
    echo "TIMESCALEDB_TELEMETRY=off"
  } >"${database_environment}"
fi
chmod 0600 "${database_environment}" "${application_environment}"

database_password="$(sed -n 's/^POSTGRES_PASSWORD=//p' "${database_environment}")"
grep -qx "POSTGRES_USER=${database_user}" "${database_environment}"
grep -qx "POSTGRES_DB=${database_name}" "${database_environment}"
[[ "${database_password}" =~ ^[a-f0-9]{64}$ ]]
database_url="postgresql://${database_user}:${database_password}@vento-nse-timescale:5432/${database_name}"
if grep -q '^MARKET_DATA_DATABASE_URL=' "${application_environment}"; then
  sed -i "s|^MARKET_DATA_DATABASE_URL=.*$|MARKET_DATA_DATABASE_URL=${database_url}|" "${application_environment}"
else
  printf '\nMARKET_DATA_DATABASE_URL=%s\n' "${database_url}" >>"${application_environment}"
fi
unset database_password database_url
chmod 0600 "${application_environment}"

install -d -m 0700 /var/backups/vento-nse/timescale
install -m 0750 "${release}/web/deploy/backup-timescale.sh" /usr/local/sbin/vento-nse-timescale-backup
install -m 0750 "${release}/web/deploy/restore-timescale.sh" /usr/local/sbin/vento-nse-timescale-restore
install -m 0644 "${release}/web/deploy/vento-nse-timescale.service" /etc/systemd/system/vento-nse-timescale.service
install -m 0644 "${release}/web/deploy/vento-nse-timescale-backup.service" /etc/systemd/system/vento-nse-timescale-backup.service
install -m 0644 "${release}/web/deploy/vento-nse-timescale-backup.timer" /etc/systemd/system/vento-nse-timescale-backup.timer

systemctl daemon-reload
systemctl enable --now vento-nse-timescale.service

for _ in {1..90}; do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' vento-nse-timescale 2>/dev/null || true)" == "healthy" ]]; then
    break
  fi
  sleep 1
done
test "$(docker inspect --format '{{.State.Health.Status}}' vento-nse-timescale)" = "healthy"

docker run --rm \
  --network vento-nse-internal \
  --env-file "${application_environment}" \
  vento-nse-backtest:current \
  python market_data_admin.py migrate

systemctl enable --now vento-nse-timescale-backup.timer
systemctl --no-pager --full status vento-nse-timescale.service
systemctl list-timers vento-nse-timescale-backup.timer --no-pager
