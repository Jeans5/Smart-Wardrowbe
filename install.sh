#!/usr/bin/env bash
# One-step home install of Smart Wardrowbe on the machine that will run it (Linux or macOS with
# Docker). It writes .env (Google Gemini as the AI, passwordless household sign-in, this
# machine's address for the tablet), builds the app from this repo, starts it and prepares the
# database. Running it again keeps the existing .env and only rebuilds and updates.
#
# Non-interactive use: set GEMINI_API_KEY, HOUSEHOLD_PROFILES and optionally SERVER_ADDRESS
# in the environment and the matching questions are skipped.

set -euo pipefail

cd "$(dirname "$0")"
ENV_FILE=".env"
FRONTEND_PORT_DEFAULT=3000

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# --- Requirements -----------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "Docker ontbreekt. Installeer Docker (Docker Desktop op Mac/Windows, docker.com op Linux) en start dit script opnieuw."
docker compose version >/dev/null 2>&1 || fail "'docker compose' werkt niet. Installeer Docker Compose v2 of een recente Docker Desktop."
docker info >/dev/null 2>&1 || fail "Docker draait niet (of je hebt geen rechten). Start Docker en probeer opnieuw."

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

detect_address() {
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')
  fi
  if [ -z "$ip" ] && command -v hostname >/dev/null 2>&1; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}') || true
  fi
  if [ -z "$ip" ] && command -v ipconfig >/dev/null 2>&1; then
    ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
  fi
  printf '%s' "${ip:-localhost}"
}

# Sets KEY=value in .env: replaces the first active or commented-out KEY= line, else appends.
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" '
    !done && ($0 ~ "^" k "=" || $0 ~ "^# ?" k "=") { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

# --- Configuration ----------------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  say "Bestaande .env gevonden: die blijft zoals ze is."
else
  say "Smart Wardrowbe instellen"

  gemini_key="${GEMINI_API_KEY:-}"
  if [ -z "$gemini_key" ]; then
    echo "Gratis Gemini-key aanmaken: https://aistudio.google.com/apikey"
    read -rsp "Plak je Gemini API-key (wordt niet getoond, Enter = later): " gemini_key
    echo
  fi

  profiles="${HOUSEHOLD_PROFILES:-}"
  if [ -z "$profiles" ]; then
    read -rp "Namen van de gezinsleden, gescheiden door komma's (bv. Anna, Bram, Cas): " profiles
  fi

  address="${SERVER_ADDRESS:-}"
  if [ -z "$address" ]; then
    detected="$(detect_address)"
    read -rp "Adres van deze computer in je wifi [$detected]: " address
    address="${address:-$detected}"
  fi
  port="${FRONTEND_PORT:-$FRONTEND_PORT_DEFAULT}"
  app_url="http://${address}:${port}"

  cp .env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  # Passwordless sign-in for the household picker; fine on a private home network only.
  set_env DEBUG true
  set_env SECRET_KEY "$(random_hex)"
  set_env NEXTAUTH_SECRET "$(random_hex)"
  set_env POSTGRES_PASSWORD "$(random_hex)"
  set_env NEXTAUTH_URL "$app_url"
  set_env APP_URL "$app_url"
  set_env FRONTEND_PORT "$port"
  if [ -n "$profiles" ]; then
    set_env HOUSEHOLD_PROFILES "\"$profiles\""
  fi

  if [ -n "$gemini_key" ]; then
    set_env AI_BASE_URL "https://generativelanguage.googleapis.com/v1beta/openai"
    set_env AI_API_KEY "$gemini_key"
    set_env AI_VISION_MODEL "gemini-flash-latest"
    set_env AI_TEXT_MODEL "gemini-flash-latest"
    # The free tier is rate limited, so analyse one photo at a time.
    set_env AI_TAGGING_CONCURRENCY 1
  else
    echo "Geen Gemini-key: zet hem later in .env (AI_* lijnen, zie 'Option 5') en run dit script opnieuw."
  fi

  say ".env aangemaakt voor $app_url"
fi

# --- Build, start, migrate --------------------------------------------------------------------
say "App bouwen en starten (de eerste keer duurt dit enkele minuten)..."
docker compose up -d --build

say "Database klaarzetten..."
for attempt in $(seq 1 30); do
  if docker compose exec -T backend alembic upgrade head; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    fail "De backend kwam niet op tijd online. Bekijk de logs met: docker compose logs backend --tail=50"
  fi
  sleep 5
done

app_url="$(awk -F= '/^NEXTAUTH_URL=/{print $2; exit}' "$ENV_FILE")"
say "Klaar! Open op de tablet: ${app_url}"
echo "Tip: kies in de browser 'Toevoegen aan beginscherm' voor een app-icoon."
echo "Lukt het niet vanaf de tablet? Controleer dat poort ${app_url##*:} niet geblokkeerd wordt door de firewall van deze computer."
