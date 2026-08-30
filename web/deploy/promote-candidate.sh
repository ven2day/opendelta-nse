#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
candidate_port="${2:-3100}"

if [[ ! "$candidate_port" =~ ^[0-9]+$ ]] || (( candidate_port < 1024 || candidate_port > 65535 )); then
  echo "candidate port must be an integer between 1024 and 65535" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/root/vento-nse-backups/${timestamp}"
nginx_site="/etc/nginx/sites-available/nse.ventoday.com.conf"
nginx_candidate="$(mktemp)"
previous_container="vento-nse-previous-${timestamp}"
old_renamed=false
candidate_renamed=false

candidate_binding="$(docker port vento-nse-candidate 3000/tcp 2>/dev/null || true)"
if [[ "$candidate_binding" != "127.0.0.1:${candidate_port}" ]]; then
  echo "vento-nse-candidate is not published on 127.0.0.1:${candidate_port}" >&2
  rm -f "$nginx_candidate"
  exit 1
fi

curl -fsS -o /dev/null "http://127.0.0.1:${candidate_port}/login"

candidate_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' vento-nse-candidate)"
if [[ "${candidate_health}" != "healthy" ]]; then
  echo "vento-nse-candidate is not healthy: ${candidate_health}" >&2
  rm -f "$nginx_candidate"
  exit 1
fi

install -d -m 0700 "${backup_dir}"
cp -a "${nginx_site}" "${backup_dir}/nse.ventoday.com.conf"

rollback() {
  status=$?
  if [[ ${status} -ne 0 ]]; then
    if [[ "$candidate_renamed" == true ]]; then
      docker rename vento-nse vento-nse-candidate >/dev/null 2>&1 || true
      docker update --restart no vento-nse-candidate >/dev/null 2>&1 || true
    fi
    if [[ "$old_renamed" == true ]]; then
      docker rename "$previous_container" vento-nse >/dev/null 2>&1 || true
      docker start vento-nse >/dev/null 2>&1 || true
    fi
    cp -a "${backup_dir}/nse.ventoday.com.conf" "${nginx_site}"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  fi
  rm -f "$nginx_candidate"
  exit "${status}"
}
trap rollback EXIT

sed "s#http://127.0.0.1:3100#http://127.0.0.1:${candidate_port}#g" \
  "/opt/vento-nse/releases/${release_id}/web/deploy/nse.ventoday.com.nginx.conf" \
  > "$nginx_candidate"

install -m 0644 \
  "$nginx_candidate" \
  "${nginx_site}"

nginx -t
systemctl reload nginx
curl -fsS -o /dev/null \
  --resolve nse.ventoday.com:443:127.0.0.1 \
  https://nse.ventoday.com/login

docker update --restart unless-stopped vento-nse-candidate >/dev/null
docker rename vento-nse "$previous_container"
old_renamed=true
docker rename vento-nse-candidate vento-nse
candidate_renamed=true
ln -sfn "/opt/vento-nse/releases/${release_id}" /opt/vento-nse/current
curl -fsS -o /dev/null \
  --resolve nse.ventoday.com:443:127.0.0.1 \
  https://nse.ventoday.com/login

docker stop "$previous_container" >/dev/null

trap - EXIT
rm -f "$nginx_candidate"

echo "candidate promoted; backup=${backup_dir}; previous=${previous_container}"
