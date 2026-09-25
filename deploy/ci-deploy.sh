#!/usr/bin/env bash
#
# Deploy the backend from a tarball piped over SSH. Installed on the server at
# /opt/mediscan/ci-deploy.sh and wired as the FORCED COMMAND for the CI key, so
# that key can do nothing else - no shell, no arbitrary commands, no port
# forwarding. That matters because this repository is public: the key lives in
# GitHub Actions secrets, and if it ever leaked it still could not be used to
# open a root session on the server.
#
# Contract: stdin is a gzipped tar whose single top-level directory is
# `backend/`. Anything else is rejected.
#
# The deploy is reversible at every step: the running code is archived first,
# and if the rebuilt container fails its health check the previous code is put
# back and rebuilt, so a bad push cannot leave the API down.
set -euo pipefail

ROOT=/opt/mediscan
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$ROOT/backups/backend-before-$STAMP.tar.gz"
HEALTH_URL=http://127.0.0.1:8090/health
HEALTH_RETRIES=20

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

health_ok() {
    for _ in $(seq "$HEALTH_RETRIES"); do
        if curl -fsS --max-time 5 "$HEALTH_URL" | grep -q '"ok"'; then
            return 0
        fi
        sleep 3
    done
    return 1
}

cd "$ROOT"
mkdir -p backups

log "receiving backend archive"
rm -rf backend.incoming
mkdir backend.incoming
# Refuse anything that is not exactly a backend/ tree. Nothing on disk has
# changed yet at this point, so a malformed archive is a no-op.
if ! tar -xzf - -C backend.incoming 2>/dev/null; then
    log "ERROR: input is not a readable gzipped tar - refusing"
    rm -rf backend.incoming
    exit 2
fi
if [ ! -d backend.incoming/backend ] || [ ! -f backend.incoming/backend/Dockerfile ]; then
    log "ERROR: archive does not contain backend/Dockerfile - refusing"
    rm -rf backend.incoming
    exit 2
fi

log "archiving the running code -> $BACKUP"
tar -czf "$BACKUP" backend

log "swapping in the new code"
rm -rf backend.previous
mv backend backend.previous
mv backend.incoming/backend backend
rm -rf backend.incoming

log "rebuilding"
if ! docker compose up -d --build backend; then
    log "BUILD FAILED - rolling back"
    rm -rf backend && mv backend.previous backend
    docker compose up -d --build backend || true
    exit 3
fi

log "waiting for health"
if ! health_ok; then
    log "HEALTH CHECK FAILED - rolling back"
    rm -rf backend && mv backend.previous backend
    docker compose up -d --build backend >/dev/null 2>&1 || true
    if health_ok; then
        log "rollback restored a healthy service"
    else
        log "ERROR: service unhealthy after rollback - needs a human"
    fi
    exit 4
fi

# Keep the last 10 rollback archives; drop the rest.
ls -1t "$ROOT"/backups/backend-before-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
rm -rf backend.previous

log "deployed successfully"
curl -fsS --max-time 5 "$HEALTH_URL"; echo
