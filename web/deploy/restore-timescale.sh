#!/usr/bin/env bash
set -euo pipefail

backup_directory="/var/backups/opendelta/timescale"
container="opendelta-timescale"
confirmation="--confirm-restore-opendelta"

if [[ "$#" -ne 2 || "$2" != "${confirmation}" ]]; then
  echo "Usage: $0 /var/backups/opendelta/timescale/opendelta-TIMESTAMP.dump ${confirmation}" >&2
  exit 2
fi

dump_path="$(realpath -- "$1")"
case "${dump_path}" in
  "${backup_directory}"/opendelta-*.dump) ;;
  *) echo "Restore input must be an OpenDelta dump in ${backup_directory}" >&2; exit 2 ;;
esac
test -f "${dump_path}"
test -s "${dump_path}"
docker inspect --format '{{.State.Health.Status}}' "${container}" | grep -qx healthy
docker exec -i "${container}" pg_restore --list <"${dump_path}" >/dev/null

backup_command="/usr/local/sbin/opendelta-timescale-backup"
if [[ ! -x "${backup_command}" ]]; then
  backup_command="$(dirname "$0")/backup-timescale.sh"
fi
"${backup_command}"

restart_services=()
for service in opendelta-market-data-worker.timer opendelta-backtest.service; do
  if systemctl is-active --quiet "${service}"; then
    restart_services+=("${service}")
    systemctl stop "${service}"
  fi
done
restart_dependencies() {
  for service in "${restart_services[@]}"; do
    systemctl start "${service}"
  done
}
trap restart_dependencies EXIT

docker exec "${container}" sh -ceu \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 --command "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '\''$POSTGRES_DB'\'' AND pid <> pg_backend_pid();"'
docker exec -i "${container}" sh -ceu \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' \
  <"${dump_path}"

echo "Restored TimescaleDB from verified dump: ${dump_path}"
