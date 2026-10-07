#!/usr/bin/env bash
# Rotate the production Postgres password, safely, in one step.
#
# Run from your machine:
#   ssh -i ~/.ssh/axm_deploy root@mediscan.maxxcom.in 'bash -s' < deploy/rotate-db-password.sh
#
# What it does, on the server, in /opt/mediscan:
#   1. reads the CURRENT password and user from the running db container;
#   2. backs up every config file that contains the password;
#   3. generates a new random password ON THE SERVER - it is never printed,
#      logged, or sent anywhere;
#   4. changes it in Postgres (ALTER USER), then in the config files;
#   5. recreates the containers so the API connects with the new password;
#   6. waits for /health. If the API does not come back healthy, EVERYTHING is
#      rolled back: the old password restored in Postgres and the config files
#      put back as they were.
#
# Safe to re-run. It prints only progress lines - never a password.
set -euo pipefail

ROOT=/opt/mediscan
cd "$ROOT"
stamp=$(date +%Y%m%d-%H%M%S)
log() { printf '[rotate-db-password %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

db_env() { docker compose exec -T db printenv "$1" | tr -d '\r\n'; }
OLD=$(db_env POSTGRES_PASSWORD)
DBUSER=$(db_env POSTGRES_USER)
[ -n "$OLD" ] || { log "could not read the current password from the db container - nothing changed"; exit 1; }
[ -n "$DBUSER" ] || DBUSER=postgres

# Hex only: safe inside a DATABASE_URL, a YAML value and a shell string.
NEW=$(openssl rand -hex 24)

# Every config file under the deployment that carries the old password.
mapfile -t FILES < <(grep -rlF --exclude-dir=backups --exclude-dir=backend \
                      --exclude-dir=backend.incoming -- "$OLD" . 2>/dev/null || true)
[ "${#FILES[@]}" -gt 0 ] || { log "the password is in no config file here - nothing changed"; exit 1; }
for f in "${FILES[@]}"; do
  cp -p "$f" "$f.before-rotate-$stamp"
  log "backed up $f"
done

psql_set() {
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$DBUSER" -d postgres \
    -c "ALTER USER \"$DBUSER\" WITH PASSWORD '$1';" >/dev/null
}

rollback() {
  log "ROLLING BACK"
  psql_set "$OLD" || log "WARNING: could not restore the old password in Postgres"
  for f in "${FILES[@]}"; do cp -p "$f.before-rotate-$stamp" "$f"; done
  docker compose up -d >/dev/null 2>&1 || true
  log "rolled back - the old password is in force, the API restarted with it"
}

log "changing the password in Postgres"
psql_set "$NEW"

log "writing it into ${#FILES[@]} config file(s)"
for f in "${FILES[@]}"; do
  OLD="$OLD" NEW="$NEW" python3 - "$f" <<'PY'
import os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text.replace(os.environ["OLD"], os.environ["NEW"]))
PY
done

log "recreating the containers with the new password"
if ! docker compose up -d >/dev/null 2>&1; then
  rollback; exit 1
fi

log "waiting for the API to report healthy"
for _ in $(seq 1 30); do
  # Asked from inside the container, on the port it actually listens on
  # (backend/Dockerfile: ${PORT:-8080}) - nothing assumed about what the host
  # publishes.
  if docker compose exec -T backend python -c \
       "import os,urllib.request,sys; port=os.environ.get('PORT','8080'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/health',timeout=5).status==200 else 1)" \
       >/dev/null 2>&1; then
    # Healthy is not enough: prove the API can actually reach the database.
    if docker compose exec -T backend python -c \
        "from app.database import SessionLocal; from sqlalchemy import text; s=SessionLocal(); s.execute(text('select 1')); s.close()" \
        >/dev/null 2>&1; then
      log "done - the API is healthy and connected with the new password"
      log "backups of the old config: *.before-rotate-$stamp (delete them once you are satisfied)"
      exit 0
    fi
  fi
  sleep 4
done

log "the API did not come back healthy on the new password"
rollback
exit 1
