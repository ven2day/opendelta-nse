#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id is required}"

if [[ -n "$(docker ps -aq --filter name='^/vento-nse-candidate$')" ]]; then
  echo "vento-nse-candidate container already exists" >&2
  exit 1
fi

if ! docker network inspect vento-nse-internal >/dev/null 2>&1; then
  docker network create vento-nse-internal >/dev/null
fi

docker run -d \
  --name vento-nse-candidate \
  --restart no \
  --network vento-nse-internal \
  --env-file /etc/vento-nse.env \
  --env BACKTEST_SERVICE_URL=http://vento-nse-backtest:8000 \
  --publish 127.0.0.1:3100:3000 \
  --mount type=bind,source=/var/lib/vento-nse/data,target=/app/web/dist/client/live,readonly \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /app/web/.wrangler:rw,noexec,nosuid,uid=1000,gid=1000,size=16m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  "vento-nse-dashboard:${release_id}"

for _ in {1..20}; do
  if curl -fsS -o /dev/null http://127.0.0.1:3100/login; then
    break
  fi
  sleep 1
done

curl -fsS -o /dev/null http://127.0.0.1:3100/login
docker ps --filter name='^/vento-nse-candidate$' --format '{{.Names}} {{.Status}} {{.Ports}}'
