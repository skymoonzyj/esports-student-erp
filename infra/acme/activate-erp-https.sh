#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="${ERP_TLS_BASE_DIR:-/home/skymoonzyj/.config/esports-erp}"
RELEASE_DIR="${ERP_RELEASE_DIR:-/home/skymoonzyj/esports-erp/current}"
PROJECT="${ERP_COMPOSE_PROJECT:-esports-erp-staging}"

"$BASE_DIR/acme/renew-erp-tls.sh"

if [[ ! -s "$BASE_DIR/certs/fullchain.pem" || ! -s "$BASE_DIR/certs/privkey.pem" ]]; then
  echo "Certificate is not available yet; keeping the current service unchanged"
  exit 0
fi

# The public URL is used for generated links and verification callbacks.
if grep -q '^ERP_WEB_BASE_URL=' "$RELEASE_DIR/.env" 2>/dev/null; then
  sed -i 's#^ERP_WEB_BASE_URL=.*#ERP_WEB_BASE_URL=https://skymoonzyj.l.cd:18080#' "$RELEASE_DIR/.env"
else
  printf '%s\n' 'ERP_WEB_BASE_URL=https://skymoonzyj.l.cd:18080' >> "$RELEASE_DIR/.env"
fi

cd "$RELEASE_DIR"
compose=(
  docker compose -p "$PROJECT"
  -f compose.yaml
  -f compose.prod.yaml
  -f compose.nas-staging.yaml
  -f compose.nas-lan-staging.yaml
)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --remove-orphans
echo "ERP HTTPS compose services activated on port 18080"
