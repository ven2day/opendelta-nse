#!/usr/bin/env bash
set -euo pipefail

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/root/opendelta-backups/${timestamp}"
nanodelta_override="/root/nanodelta/docker-compose.override.yml"
nginx_site="/etc/nginx/sites-available/delta.ventoday.com.conf"

install -d -m 0700 "${backup_dir}"
cp -a "${nanodelta_override}" "${backup_dir}/docker-compose.override.yml"
cp -a "${nginx_site}" "${backup_dir}/delta.ventoday.com.conf"

rollback() {
  status=$?
  if [[ ${status} -ne 0 ]]; then
    cp -a "${backup_dir}/docker-compose.override.yml" "${nanodelta_override}"
    cp -a "${backup_dir}/delta.ventoday.com.conf" "${nginx_site}"
    systemctl stop nginx >/dev/null 2>&1 || true
    (
      cd /root/nanodelta
      docker compose up -d --no-deps --force-recreate web
    ) >/dev/null 2>&1 || true
  fi
  exit "${status}"
}
trap rollback EXIT

install -m 0644 \
  /opt/opendelta/current/web/deploy/nanodelta-web-local.override.yml \
  "${nanodelta_override}"

(
  cd /root/nanodelta
  docker compose up -d --no-deps --force-recreate web
)

for _ in {1..30}; do
  if curl -fsS -o /dev/null http://127.0.0.1:3002/; then
    break
  fi
  sleep 1
done
curl -fsS -o /dev/null http://127.0.0.1:3002/

install -m 0644 \
  /opt/opendelta/current/web/deploy/delta.ventoday.com.nginx.conf \
  "${nginx_site}"

nginx -t
systemctl enable --now nginx

curl -fsS -o /dev/null \
  --resolve delta.ventoday.com:443:127.0.0.1 \
  https://delta.ventoday.com/login

trap - EXIT
echo "cutover verification passed; backup=${backup_dir}"
