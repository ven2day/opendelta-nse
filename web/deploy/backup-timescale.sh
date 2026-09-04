#!/usr/bin/env bash
set -euo pipefail

backup_directory="/var/backups/opendelta/timescale"
container="opendelta-timescale"
retention_days="${TIMESCALE_BACKUP_RETENTION_DAYS:-30}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_path="${backup_directory}/opendelta-${timestamp}.dump"
temporary_path="${final_path}.partial"

if ! [[ "${retention_days}" =~ ^[0-9]+$ ]] || (( retention_days < 7 )); then
  echo "TIMESCALE_BACKUP_RETENTION_DAYS must be an integer of at least 7" >&2
  exit 1
fi

install -d -m 0700 "${backup_directory}"
trap 'rm -f -- "${temporary_path}"' EXIT

docker inspect --format '{{.State.Health.Status}}' "${container}" | grep -qx healthy
docker exec "${container}" sh -ceu \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --compress=6 --no-owner' \
  >"${temporary_path}"
test -s "${temporary_path}"
docker exec -i "${container}" pg_restore --list <"${temporary_path}" >/dev/null
chmod 0600 "${temporary_path}"
mv -- "${temporary_path}" "${final_path}"
trap - EXIT

find "${backup_directory}" -maxdepth 1 -type f -name 'opendelta-*.dump' \
  -mtime "+${retention_days}" -delete

echo "Created verified TimescaleDB backup: ${final_path}"
