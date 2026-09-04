#!/usr/bin/env bash
set -euo pipefail

release="/opt/opendelta/current"
database_environment="/etc/opendelta-timescale.env"
application_environment="/etc/opendelta-dhan.env"
database_user="opendelta"
database_name="opendelta"

test "$(id -u)" -eq 0
test -f "${application_environment}"
for asset in \
  opendelta-timescale.service \
  opendelta-timescale-backup.service \
  opendelta-timescale-backup.timer \
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
database_url="postgresql://${database_user}:${database_password}@opendelta-timescale:5432/${database_name}"
if grep -q '^MARKET_DATA_DATABASE_URL=' "${application_environment}"; then
  sed -i "s|^MARKET_DATA_DATABASE_URL=.*$|MARKET_DATA_DATABASE_URL=${database_url}|" "${application_environment}"
else
  printf '\nMARKET_DATA_DATABASE_URL=%s\n' "${database_url}" >>"${application_environment}"
fi
unset database_password database_url
chmod 0600 "${application_environment}"

install -d -m 0700 /var/backups/opendelta/timescale
install -m 0750 "${release}/web/deploy/backup-timescale.sh" /usr/local/sbin/opendelta-timescale-backup
install -m 0750 "${release}/web/deploy/restore-timescale.sh" /usr/local/sbin/opendelta-timescale-restore
install -m 0644 "${release}/web/deploy/opendelta-timescale.service" /etc/systemd/system/opendelta-timescale.service
install -m 0644 "${release}/web/deploy/opendelta-timescale-backup.service" /etc/systemd/system/opendelta-timescale-backup.service
install -m 0644 "${release}/web/deploy/opendelta-timescale-backup.timer" /etc/systemd/system/opendelta-timescale-backup.timer

systemctl daemon-reload
systemctl enable --now opendelta-timescale.service

for _ in {1..90}; do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' opendelta-timescale 2>/dev/null || true)" == "healthy" ]]; then
    break
  fi
  sleep 1
done
test "$(docker inspect --format '{{.State.Health.Status}}' opendelta-timescale)" = "healthy"

docker run --rm \
  --network opendelta-internal \
  --env-file "${application_environment}" \
  opendelta-backtest:current \
  python -m backend.data.admin migrate

systemctl enable --now opendelta-timescale-backup.timer
systemctl --no-pager --full status opendelta-timescale.service
systemctl list-timers opendelta-timescale-backup.timer --no-pager
