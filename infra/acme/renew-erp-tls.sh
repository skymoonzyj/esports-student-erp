#!/usr/bin/env bash
set -Eeuo pipefail

# Host-side certificate runner.  Secrets are mounted as files into the
# short-lived Certbot container; they are never placed in command arguments.

BASE_DIR="${ERP_TLS_BASE_DIR:-/home/skymoonzyj/.config/esports-erp}"
ACME_DIR="$BASE_DIR/acme"
CERT_DIR="${ERP_TLS_CERT_DIR:-$BASE_DIR/certs}"
SECRET_DIR="$BASE_DIR/secrets"
HOOK="$ACME_DIR/dnshe_certbot_hook.py"
DOMAIN="${ERP_TLS_DOMAIN:-skymoonzyj.l.cd}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:latest}"
CADDY_CONTAINER="${CADDY_CONTAINER:-esports-erp-staging-caddy-1}"

mkdir -p "$ACME_DIR" "$CERT_DIR"
chmod 700 "$ACME_DIR" "$CERT_DIR"

KEY_FILE="$SECRET_DIR/dnshe_api_key"
SECRET_FILE="$SECRET_DIR/dnshe_api_secret"
if [[ ! -r "$KEY_FILE" || ! -r "$SECRET_FILE" ]]; then
  echo "DNSHE API credential files are missing" >&2
  exit 1
fi
if [[ ! -r "$HOOK" ]]; then
  echo "DNSHE Certbot hook is missing" >&2
  exit 1
fi

LOCK_DIR="$BASE_DIR/.erp-tls-renew.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "ERP TLS renewal is already running"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

if ! docker image inspect "$CERTBOT_IMAGE" >/dev/null 2>&1; then
  docker pull "$CERTBOT_IMAGE" >/dev/null
fi

certbot_run() {
  docker run --rm --pull never \
    --name esports-erp-certbot \
    -e DNSHE_API_KEY_FILE=/run/secrets/dnshe_api_key \
    -e DNSHE_API_SECRET_FILE=/run/secrets/dnshe_api_secret \
    -e DNSHE_PROPAGATION_SECONDS="${DNSHE_PROPAGATION_SECONDS:-60}" \
    -v "$ACME_DIR:/etc/letsencrypt" \
    -v "$HOOK:/opt/dnshe_certbot_hook.py:ro" \
    -v "$KEY_FILE:/run/secrets/dnshe_api_key:ro" \
    -v "$SECRET_FILE:/run/secrets/dnshe_api_secret:ro" \
    "$CERTBOT_IMAGE" "$@"
}

COMMON_ARGS=(
  --manual
  --preferred-challenges dns
  --manual-auth-hook "python3 /opt/dnshe_certbot_hook.py add"
  --manual-cleanup-hook "python3 /opt/dnshe_certbot_hook.py cleanup"
  --non-interactive
  --agree-tos
  --register-unsafely-without-email
  --no-eff-email
  --cert-name "$DOMAIN"
)

if [[ ! -s "$ACME_DIR/live/$DOMAIN/fullchain.pem" ]]; then
  certbot_run certonly "${COMMON_ARGS[@]}" -d "$DOMAIN"
else
  certbot_run renew "${COMMON_ARGS[@]}"
fi

SOURCE_DIR="$ACME_DIR/live/$DOMAIN"
if [[ -r "$SOURCE_DIR/fullchain.pem" && -r "$SOURCE_DIR/privkey.pem" ]]; then
  install -m 0644 "$SOURCE_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
  install -m 0600 "$SOURCE_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
else
  # Certbot runs as root in its container, so its private archive may be
  # intentionally unreadable by the NAS login user.  Export through a
  # short-lived root container instead of weakening the archive permissions.
  docker run --rm --pull never \
    --entrypoint sh \
    -e ERP_TLS_DOMAIN="$DOMAIN" \
    -v "$ACME_DIR:/etc/letsencrypt:ro" \
    -v "$CERT_DIR:/export" \
    "$CERTBOT_IMAGE" -c '
      set -eu
      source_dir="/etc/letsencrypt/live/$ERP_TLS_DOMAIN"
      test -s "$source_dir/fullchain.pem"
      test -s "$source_dir/privkey.pem"
      cp "$source_dir/fullchain.pem" /export/fullchain.pem.tmp
      cp "$source_dir/privkey.pem" /export/privkey.pem.tmp
      chmod 0644 /export/fullchain.pem.tmp
      chmod 0600 /export/privkey.pem.tmp
      mv -f /export/fullchain.pem.tmp /export/fullchain.pem
      mv -f /export/privkey.pem.tmp /export/privkey.pem
    '
fi

if [[ ! -s "$CERT_DIR/fullchain.pem" || ! -s "$CERT_DIR/privkey.pem" ]]; then
  echo "Certbot did not produce an exportable certificate" >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -Fxq "$CADDY_CONTAINER"; then
  docker exec "$CADDY_CONTAINER" caddy reload \
    --config /etc/caddy/Caddyfile \
    --adapter caddyfile >/dev/null
fi

echo "ERP TLS certificate is installed for $DOMAIN"
