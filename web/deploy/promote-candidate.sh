#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/root/vento-nse-backups/${timestamp}"
nginx_site="/etc/nginx/sites-available/nse.ventoday.com.conf"

install -d -m 0700 "${backup_dir}"
cp -a "${nginx_site}" "${backup_dir}/nse.ventoday.com.conf"

rollback() {
  status=$?
  if [[ ${status} -ne 0 ]]; then
    cp -a "${backup_dir}/nse.ventoday.com.conf" "${nginx_site}"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi
  exit "${status}"
}
trap rollback EXIT

install -m 0644 \
  "/opt/vento-nse/releases/${release_id}/web/deploy/nse.ventoday.com.nginx.conf" \
  "${nginx_site}"

nginx -t
systemctl reload nginx
curl -fsS -o /dev/null \
  --resolve nse.ventoday.com:443:127.0.0.1 \
  https://nse.ventoday.com/login

trap - EXIT

docker update --restart unless-stopped vento-nse-candidate >/dev/null
docker stop vento-nse >/dev/null
docker rm vento-nse >/dev/null
docker rename vento-nse-candidate vento-nse
ln -sfn "/opt/vento-nse/releases/${release_id}" /opt/vento-nse/current

echo "candidate promoted; backup=${backup_dir}"
