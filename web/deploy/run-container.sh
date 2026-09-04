#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"
candidate_port="${2:-3100}"

if [[ ! "$candidate_port" =~ ^[0-9]+$ ]] || (( candidate_port < 1024 || candidate_port > 65535 )); then
  echo "candidate port must be an integer between 1024 and 65535" >&2
  exit 1
fi

if [[ -n "$(docker ps -aq --filter name='^/opendelta-candidate$')" ]]; then
  echo "opendelta-candidate container already exists" >&2
  exit 1
fi

if ! docker network inspect opendelta-internal >/dev/null 2>&1; then
  docker network create opendelta-internal >/dev/null
fi

docker run -d \
  --name opendelta-candidate \
  --restart no \
  --network opendelta-internal \
  --env-file /etc/opendelta.env \
  --env BACKTEST_SERVICE_URL=http://opendelta-backtest:8000 \
  --publish "127.0.0.1:${candidate_port}:3000" \
  --mount type=bind,source=/var/lib/opendelta/data,target=/app/web/dist/client/live,readonly \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /app/web/.wrangler:rw,noexec,nosuid,uid=1000,gid=1000,size=16m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  "opendelta-dashboard:${release_id}"

for _ in {1..20}; do
  if curl -fsS -o /dev/null "http://127.0.0.1:${candidate_port}/login"; then
    break
  fi
  sleep 1
done

curl -fsS -o /dev/null "http://127.0.0.1:${candidate_port}/login"

candidate_health="starting"
for _ in {1..30}; do
  candidate_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' opendelta-candidate)"
  if [[ "${candidate_health}" == "healthy" ]]; then
    break
  fi
  sleep 1
done

if [[ "${candidate_health}" != "healthy" ]]; then
  echo "opendelta-candidate did not become healthy: ${candidate_health}" >&2
  exit 1
fi

docker ps --filter name='^/opendelta-candidate$' --format '{{.Names}} {{.Status}} {{.Ports}}'
