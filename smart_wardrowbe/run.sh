#!/usr/bin/env bash
# Starts every part of Smart Wardrowbe inside the one Home Assistant app container: PostgreSQL,
# Redis, the API, both workers and the web frontend. All state lives in /data, which Home
# Assistant keeps across restarts, updates and backups. If any part stops, everything is shut
# down cleanly and the script exits non-zero so the watchdog can restart the app.
set -euo pipefail

OPTIONS=/data/options.json
PG_SOCKET=/run/postgresql
PGDATA=/data/postgres

log() { echo "[smart-wardrowbe] $*"; }

option() {
  jq -r --arg key "$1" '.[$key] // empty' "$OPTIONS" 2>/dev/null || true
}

random_hex() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# --- Settings from the app's Configuration tab --------------------------------------------------
GEMINI_API_KEY="$(option gemini_api_key)"
HOUSEHOLD_PROFILES="$(option household_profiles)"
APP_URL="$(option app_url)"
APP_URL="${APP_URL%/}"
APP_URL="${APP_URL:-http://homeassistant.local:3000}"
AI_MODEL="$(option ai_model)"
AI_MODEL="${AI_MODEL:-gemini-flash-latest}"
PHOTO_MODEL="$(option photo_model)"
PHOTO_MODEL="${PHOTO_MODEL:-gemini-3.1-flash-image-preview}"
PHOTO_RESOLUTION="$(option photo_resolution)"
PHOTO_RESOLUTION="${PHOTO_RESOLUTION:-1K}"
# Not option(): its "// empty" turns a false into nothing, which would read as the default.
PRODUCT_PHOTOS="$(jq -r 'if has("product_photos") then .product_photos else true end' "$OPTIONS" 2>/dev/null || echo true)"

mkdir -p /data/wardrobe /data/uploads /data/redis "$PG_SOCKET"

# Secrets are generated once and kept, so sessions and the database survive restarts.
SECRETS=/data/secrets.env
if [ ! -f "$SECRETS" ]; then
  log "First start: generating secrets"
  (
    umask 077
    printf 'SECRET_KEY=%s\nNEXTAUTH_SECRET=%s\nPOSTGRES_PASSWORD=%s\n' \
      "$(random_hex)" "$(random_hex)" "$(random_hex)" > "$SECRETS"
  )
fi
# shellcheck disable=SC1090
. "$SECRETS"

# --- PostgreSQL ---------------------------------------------------------------------------------
# shellcheck disable=SC2012
PG_BIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -n 1)"
chown postgres:postgres "$PG_SOCKET"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "First start: creating the database"
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  chmod 700 "$PGDATA"
  gosu postgres "$PG_BIN/initdb" -D "$PGDATA" -U postgres \
    --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8 >/dev/null
fi
# A restored backup may come back with other owners.
if [ "$(stat -c %U "$PGDATA")" != "postgres" ]; then
  chown -R postgres:postgres "$PGDATA"
fi
chmod 700 "$PGDATA"

log "Starting PostgreSQL"
gosu postgres "$PG_BIN/postgres" -D "$PGDATA" \
  -c listen_addresses=127.0.0.1 -c unix_socket_directories="$PG_SOCKET" &
PG_PID=$!

for _ in $(seq 1 60); do
  gosu postgres "$PG_BIN/pg_isready" -q -h "$PG_SOCKET" && break
  sleep 1
done
gosu postgres "$PG_BIN/pg_isready" -q -h "$PG_SOCKET" || { log "PostgreSQL did not start"; exit 1; }

psql_admin() {
  gosu postgres "$PG_BIN/psql" -h "$PG_SOCKET" -U postgres -v ON_ERROR_STOP=1 -tA "$@"
}
if [ "$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = 'wardrobe'")" != "1" ]; then
  psql_admin -c "CREATE ROLE wardrobe LOGIN PASSWORD '${POSTGRES_PASSWORD}'" >/dev/null
fi
if [ "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = 'wardrobe'")" != "1" ]; then
  psql_admin -c "CREATE DATABASE wardrobe OWNER wardrobe" >/dev/null
fi

# --- Redis --------------------------------------------------------------------------------------
chown -R redis:redis /data/redis
log "Starting Redis"
gosu redis redis-server --bind 127.0.0.1 --port 6379 --dir /data/redis --protected-mode yes &
REDIS_PID=$!
for _ in $(seq 1 30); do
  [ "$(redis-cli -h 127.0.0.1 ping 2>/dev/null)" = "PONG" ] && break
  sleep 1
done

# --- Backend settings (the same block every backend process reads) ----------------------------
# DEBUG=true enables the passwordless sign-in behind the household profile picker. The app is
# meant for the home network only.
export DEBUG=true
export DATABASE_URL="postgresql+asyncpg://wardrobe:${POSTGRES_PASSWORD}@127.0.0.1:5432/wardrobe"
export REDIS_URL="redis://127.0.0.1:6379/0"
export SECRET_KEY
export STORAGE_PATH=/data/wardrobe
export APP_URL
export LOG_LEVEL=INFO
export AI_TAGGING_CONCURRENCY=1
# Gemini 3 models cannot switch thinking off and answer "none" with a 400; "low" keeps
# replies fast and is accepted by every current Gemini model.
export AI_REASONING_EFFORT=low
export MAX_IMAGE_MEGAPIXELS=50
export IMAGE_WORKER_CONCURRENCY=1
export BG_REMOVAL_PROVIDER=rembg
export BG_REMOVAL_MODEL=u2net
if [ -n "$GEMINI_API_KEY" ]; then
  export AI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai"
  export AI_API_KEY="$GEMINI_API_KEY"
  export AI_VISION_MODEL="$AI_MODEL"
  export AI_TEXT_MODEL="$AI_MODEL"
  # Product photos: Gemini's image model redraws each photo as a pressed catalogue shot on
  # white. It replaces the local rembg cut-out, and the original photo is always kept.
  export BG_REMOVAL_PROVIDER=gemini
  export PRODUCT_PHOTO_MODEL="$PHOTO_MODEL"
  # 2K shows a fine weave more sharply, at about half again the price per photo.
  export PRODUCT_PHOTO_IMAGE_SIZE="$PHOTO_RESOLUTION"
  if [ "$PRODUCT_PHOTOS" = "false" ]; then
    export PRODUCT_PHOTO_AUTO=false
    log "Automatic product photos are off; the product photo button still works per item"
  else
    export PRODUCT_PHOTO_AUTO=true
  fi
else
  log "No Gemini API key set: the app starts, but clothes are not recognised until you add one"
  export AI_INTERNAL_ENABLED=false
fi

cd /app
log "Updating the database schema"
alembic upgrade head

log "Starting the API and workers"
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
arq app.workers.worker.WorkerSettings &
WORKER_PID=$!
arq app.workers.image_worker.ImageWorkerSettings &
IMAGE_WORKER_PID=$!

for _ in $(seq 1 90); do
  curl -fs http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1 && break
  sleep 1
done

# --- Frontend -----------------------------------------------------------------------------------
log "Starting the web interface on ${APP_URL}"
cd /frontend
# No NEXTAUTH_URL: sign-in follows the address the browser used, so the app works both on
# the home network and over a VPN such as Tailscale.
BACKEND_URL=http://127.0.0.1:8000 \
AUTH_TRUST_HOST=true \
NEXTAUTH_SECRET="$NEXTAUTH_SECRET" \
DEV_MODE=true \
HOUSEHOLD_PROFILES="$HOUSEHOLD_PROFILES" \
NODE_ENV=production \
NEXT_TELEMETRY_DISABLED=1 \
PORT=3000 \
HOSTNAME=0.0.0.0 \
  node server.js &
FRONTEND_PID=$!

# --- Supervision --------------------------------------------------------------------------------
set +e
APP_PIDS=("$FRONTEND_PID" "$API_PID" "$WORKER_PID" "$IMAGE_WORKER_PID")

stop_all() {
  log "Stopping"
  kill -TERM "${APP_PIDS[@]}" 2>/dev/null
  wait "${APP_PIDS[@]}" 2>/dev/null
  kill -TERM "$REDIS_PID" 2>/dev/null
  # SIGINT is PostgreSQL's fast shutdown: it ends sessions and writes a clean checkpoint.
  kill -INT "$PG_PID" 2>/dev/null
  wait "$REDIS_PID" "$PG_PID" 2>/dev/null
}

trap 'stop_all; exit 0' TERM INT

log "Ready: open ${APP_URL}"
wait -n "${APP_PIDS[@]}" "$REDIS_PID" "$PG_PID"
log "A component stopped unexpectedly; shutting down so Home Assistant can restart the app"
stop_all
exit 1
