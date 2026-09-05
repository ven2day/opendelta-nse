#!/usr/bin/env bash
# Full production deploy: pulls latest main, builds a release, and cuts over
# both the backtest service and the dashboard container. Safe to re-run --
# each stage rolls back on failure before the next stage starts.
#
# Invoked by .github/workflows/deploy.yml over SSH as a forced command tied
# to a restricted deploy key (see /root/.ssh/authorized_keys on the VPS).
set -Eeuo pipefail

REPO_DIR="/root/repos/opendelta-nse"
log() { echo "[ci-deploy] $*"; }

# Alternate between two candidate ports so a new candidate never collides
# with whatever port the currently-live dashboard container is already
# bound to (promote-candidate.sh leaves that port live indefinitely).
current_port="$(docker port opendelta 3000/tcp 2>/dev/null | sed -n 's/.*:\([0-9]*\)$/\1/p' || true)"
if [[ "${current_port}" == "3100" ]]; then
  CANDIDATE_PORT=3101
else
  CANDIDATE_PORT=3100
fi

cd "${REPO_DIR}"
git fetch origin main
git checkout main
git reset --hard origin/main

release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
log "deploying release ${release_id}"

git archive --format=tar.gz -o "/tmp/opendelta-deploy-${release_id}.tar.gz" HEAD

log "building images"
"${REPO_DIR}/web/deploy/install-release.sh" "${release_id}"

previous_backtest_image="$(docker inspect --format '{{.Image}}' opendelta-backtest 2>/dev/null || true)"

log "applying platform schema migrations"
if docker run --rm \
  --network opendelta-internal \
  --env-file /etc/opendelta-dhan.env \
  opendelta-backtest:current \
  python -m backend.data.migrate; then
  log "platform schema current"
else
  migration_rc=$?
  log "schema migration exited ${migration_rc}; restoring previous backtest image"
  if [[ -n "${previous_backtest_image}" ]]; then
    docker tag "${previous_backtest_image}" opendelta-backtest:current
  fi
  exit 1
fi

log "cutting over backtest service"
if ! systemctl restart opendelta-backtest.service; then
  restart_rc=$?
  log "systemctl restart exited ${restart_rc}; retrying once"
  sleep 2
  systemctl restart opendelta-backtest.service
fi

backtest_healthy=false
for _ in $(seq 1 90); do
  status="$(docker inspect --format '{{.State.Health.Status}}' opendelta-backtest 2>/dev/null || echo starting)"
  if [[ "${status}" == "healthy" ]]; then
    backtest_healthy=true
    break
  fi
  if [[ "${status}" == "unhealthy" ]]; then
    break
  fi
  sleep 1
done

if [[ "${backtest_healthy}" != true ]]; then
  log "backtest failed to become healthy; rolling back image"
  if [[ -n "${previous_backtest_image}" ]]; then
    docker tag "${previous_backtest_image}" opendelta-backtest:current
    systemctl restart opendelta-backtest.service
  fi
  log "deploy aborted: backtest rollback complete, dashboard untouched"
  exit 1
fi
log "backtest healthy"

log "cutting over dashboard (candidate -> verify -> promote)"
docker rm -f opendelta-candidate >/dev/null 2>&1 || true

"${REPO_DIR}/web/deploy/run-container.sh" "${release_id}" "${CANDIDATE_PORT}"
"${REPO_DIR}/web/deploy/verify-container.sh" "http://127.0.0.1:${CANDIDATE_PORT}"
"${REPO_DIR}/web/deploy/promote-candidate.sh" "${release_id}" "${CANDIDATE_PORT}"

log "deploy complete: ${release_id}"
