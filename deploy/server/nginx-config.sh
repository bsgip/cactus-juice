#!/bin/bash
# Generates an nginx configuration file (via stdout) using the cactus.env values.
#
# Usage: nginx-config.sh <der|webui|http|juice> [path-to-cactus.env]
#
#   der   — DER device-facing server block; mTLS + AES-128-CCM8; routes to Traefik
#   webui — operator-facing UI server block; standard TLS (or plain HTTP); routes to cactus-ui
#   http  — top-level http block (for templating /etc/nginx/nginx.conf)
#   juice — cactus-juice UI/API server block; standard TLS (or plain HTTP) + basic auth; routes to cactus-juice containers
#
# UI_TLS_MODE (cactus.env) controls how the operator facing webui/juice server blocks are served:
#   letsencrypt (default) — HTTPS on 443 with the certbot certs at /etc/letsencrypt/live/<fqdn>/ + HTTP redirect
#   none                  — plain HTTP on 80 (eg a localhost deployment where Let's Encrypt isn't applicable)
# The DER server block (self signed IEEE 2030.5 PKI + mTLS) is unaffected by UI_TLS_MODE.

set -euo pipefail

MODE="${1:-}"
ENV_FILE="${2:-./cactus.env}"

usage() {
    echo "Usage: sudo $0 <der|webui|http|juice> [path-to-cactus.env]"
}

case "$MODE" in
    der|webui|http|juice) ;;
    *)
        echo "ERROR: unknown mode: ${MODE:-<none>}"
        usage
        exit 1
        ;;
esac

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: env file not found: $ENV_FILE"
    usage
    exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

export CACTUS_FQDN_REGEX="${CACTUS_FQDN//./\\.}"

UI_TLS_MODE="${UI_TLS_MODE:-letsencrypt}"
case "$UI_TLS_MODE" in
    letsencrypt|none) ;;
    *)
        echo "ERROR: UI_TLS_MODE must be 'letsencrypt' or 'none' (got: ${UI_TLS_MODE})" >&2
        exit 1
        ;;
esac

# We only want specific variables to substitute into our nginx conf template
# (nginx has many of its own vars we don't want to touch)
#
# ONLY the variables defined here will substitute in the template below
ENVSUBST_VARS=$(cat <<'EOF'
${CACTUS_FQDN}
${CACTUS_FQDN_REGEX}
${CACTUS_CLIENT_NOTIFICATIONS_MOUNT_POINT}
${ENVOY_PREFIX}
${CERT_ENVOY_EE_FULLCHAIN_PATH}
${CERT_ENVOY_EE_KEY_PATH}
${CERT_SERCA_PATH}
EOF
)

# The juice vhost is rendered separately so that the CACTUS modes don't depend on juice config (and vice versa)
JUICE_ENVSUBST_VARS=$(cat <<'EOF'
${JUICE_FQDN}
${JUICE_API_PORT}
${JUICE_FRONTEND_PORT}
${JUICE_HTPASSWD_PATH}
EOF
)
if [[ "$MODE" == "juice" ]]; then
    REQUIRED_VARS="$JUICE_ENVSUBST_VARS"
else
    REQUIRED_VARS="$ENVSUBST_VARS"
fi

# Lets do a bit of error checking - ensure each env variable in REQUIRED_VARS actually has a value
declare -A seen
missing_vars=()
while IFS= read -r line; do
    [[ -z "$line" ]] && continue

    var_name="${line#\$\{}"    # strip leading ${
    var_name="${var_name%\}}"  # strip trailing }

    [[ -n "${seen[$var_name]:-}" ]] && continue
    seen[$var_name]=1

    if [[ -z "${!var_name:-}" ]]; then
        missing_vars+=("$var_name")
    fi
done <<< "$REQUIRED_VARS"

if (( ${#missing_vars[@]} > 0 )); then
    echo "Error: the following required environment variables are not set or empty:" >&2
    printf '  - %s\n' "${missing_vars[@]}" >&2
    exit 1
fi

# Prints the listen (+ TLS) directives for an operator facing UI server block, according to UI_TLS_MODE
# Usage: ui_listen_config <fqdn> [dhparam]
ui_listen_config() {
    local fqdn="$1" dhparam="${2:-}"
    if [[ "$UI_TLS_MODE" == "none" ]]; then
        cat <<EOF
    # Plain HTTP (UI_TLS_MODE=none) - no Let's Encrypt certificate required
    listen 80;
    listen [::]:80;
EOF
        return
    fi

    cat <<EOF
    listen 443 ssl;
    listen [::]:443 ssl;

    # Let's Encrypt certificate (renewed via certbot; paths are standard certbot output)
    ssl_certificate     /etc/letsencrypt/live/${fqdn}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${fqdn}/privkey.pem;
EOF
    if [[ -n "$dhparam" ]]; then
        echo "    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;"
    fi
    cat <<EOF

    ssl_protocols TLSv1.2;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
EOF
}

# Prints a HTTP -> HTTPS redirect server block for an operator facing UI (nothing when UI_TLS_MODE=none)
# Usage: ui_https_redirect <fqdn>
ui_https_redirect() {
    local fqdn="$1"
    [[ "$UI_TLS_MODE" == "none" ]] && return
    cat <<EOF

# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name ${fqdn};
    return 301 https://\$host\$request_uri;
}
EOF
}

render_der() {
envsubst "$ENVSUBST_VARS" <<"EOF"
# --- DER Client domain(s)
#
# *.CACTUS_FQDN — DER device-facing; mTLS + AES-128-CCM8; routes to Traefik
#
# Traefik listens on 127.0.0.1:5001 (mapped from its container port 80).
#
# NOTE: AES-128-CCM8 (ECDHE-ECDSA-AES128-CCM8) is required by IEEE 2030.5.  It is not
# supported by standard OpenSSL builds.  nginx must be compiled against an OpenSSL version
# with CCM cipher support enabled (OpenSSL 1.1.1+ with -DOPENSSL_EXTRA_CCM or equivalent).
# Verify with: nginx -V 2>&1 | grep -o 'OpenSSL [0-9.]*'  and  openssl ciphers | grep CCM8
server {
    listen 443 ssl;
    listen [::]:443 ssl;

    # Matches any subdomain depth of ${CACTUS_FQDN} (run-123.${CACTUS_FQDN}),
    # a.b.${CACTUS_FQDN}, etc.) but NOT ${CACTUS_FQDN} itself.
    server_name ~^.+\.${CACTUS_FQDN_REGEX}$;

    # Server certificate (from pki/create-cert.sh — dnsp-chain output, fullchain PEM)
    ssl_certificate     ${CERT_ENVOY_EE_FULLCHAIN_PATH};
    ssl_certificate_key ${CERT_ENVOY_EE_KEY_PATH};

    # TLS 1.2 + CCM8 supported (last) for 2030.5 clients, not enforced.
    ssl_protocols TLSv1.2;
    ssl_ciphers ECDH+AESGCM:ECDH+CHACHA20:ECDH+AES256:ECDH+AES128:!aNULL:!aDH:!ECDH+3DES:!RSA+3DES:!MD5:!DSS:ECDHE-ECDSA-AES128-CCM8:@SECLEVEL=0;
    ssl_prefer_server_ciphers on;

    # mTLS against SERCA. Depth 3: device (MCA→MICA) or aggregator (PCA→ICA) → client cert.
    ssl_client_certificate ${CERT_SERCA_PATH};
    ssl_verify_client on;
    ssl_verify_depth 3;

    # Anything other than ${ENVOY_PREFIX} or /.well-known is not routed to the backend
    location / {
        return 404;
    }

    # Requests with the ${ENVOY_PREFIX} or /.well-known path prefix are proxied to
    # traeffic, using identical settings for both.
    location ~ ^(${ENVOY_PREFIX}|/\.well-known)(/.*)?$ {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;

        # All incoming headers are forwarded by default; these are
        # additionally set/overridden on top of that.
        proxy_pass_request_headers on;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        # Pass URL-encoded client certificate to envoy (CERT_HEADER=ssl-client-cert)
        proxy_set_header ssl-client-cert   $ssl_client_escaped_cert;
        # Epoch time when nginx started proxying upstream; cactus-runner records this
        proxy_set_header X-Request-Start   "t=${msec}";

        proxy_read_timeout 300;
        proxy_send_timeout 300;
    }
}

# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name ~^.+\.${CACTUS_FQDN_REGEX}$;
    return 301 https://$host$request_uri;
}
EOF
}

render_webui() {
UI_LISTEN="$(ui_listen_config "$CACTUS_FQDN" dhparam)" envsubst "$ENVSUBST_VARS"' ${UI_LISTEN}' <<"EOF"
# --- Web UI domain
#
# CACTUS_FQDN — operator-facing UI; standard TLS (or plain HTTP if UI_TLS_MODE=none); routes to cactus-ui
#
# cactus-ui listens on 127.0.0.1:5000 (mapped from its container port 8080).
# cactus-client-notifications listens on 127.0.0.1:5002 (mapped from its container port 8080).
server {
${UI_LISTEN}

    server_name ${CACTUS_FQDN};

    proxy_read_timeout 300;
    proxy_send_timeout 300;

    # cactus-client-notifications webhook endpoint
    location ${CACTUS_CLIENT_NOTIFICATIONS_MOUNT_POINT} {
        proxy_pass http://127.0.0.1:5002;
        proxy_http_version 1.1;
        proxy_set_header Host            $host;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Everything else goes to the cactus-ui Flask app
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ui_https_redirect "$CACTUS_FQDN"
}

render_juice() {
UI_LISTEN="$(ui_listen_config "$JUICE_FQDN")" envsubst "$JUICE_ENVSUBST_VARS"' ${UI_LISTEN}' <<"EOF"
# --- cactus-juice domain
#
# JUICE_FQDN — operator-facing cactus-juice UI + API; standard TLS (or plain HTTP if UI_TLS_MODE=none); HTTP basic auth
#
# cactus-juice-frontend listens on 127.0.0.1:${JUICE_FRONTEND_PORT} (mapped from its container port 8080).
# cactus-juice-api listens on 127.0.0.1:${JUICE_API_PORT} (mapped from its container port 8080).
#
# NOTE: If JUICE_FQDN is a subdomain of CACTUS_FQDN, this (exact) server_name takes precedence over the
# DER vhost's wildcard regex - for both the HTTP host match and the TLS SNI certificate selection.
server {
${UI_LISTEN}

    server_name ${JUICE_FQDN};

    # The juice API has no auth of its own (and manages client certs/keys + Troca credentials)
    auth_basic           "cactus-juice";
    auth_basic_user_file ${JUICE_HTPASSWD_PATH};

    proxy_read_timeout 300;
    proxy_send_timeout 300;

    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # FastAPI backend (JSON API + its interactive docs)
    location /api/ {
        proxy_pass http://127.0.0.1:${JUICE_API_PORT};
    }
    location ~ ^/(docs|redoc|openapi\.json)$ {
        proxy_pass http://127.0.0.1:${JUICE_API_PORT};
    }

    # Everything else goes to the frontend SPA
    location / {
        proxy_pass http://127.0.0.1:${JUICE_FRONTEND_PORT};
    }
}
EOF
ui_https_redirect "$JUICE_FQDN"
}

render_http() {
    # A "default" nginx.conf http block. Not yet templated against cactus.env —
    # placeholder until the top-level nginx.conf needs its own substituted values.
    cat <<"EOF"
user www-data;
worker_processes auto;
worker_cpu_affinity auto;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log;
include /etc/nginx/modules-enabled/*.conf;

events {
	worker_connections 768;
	# multi_accept on;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile           on;
    keepalive_timeout  65;

    gzip on;

    ssl_protocols TLSv1.2 TLSv1.3;

    # Logging
    log_format journal_plain '[$time_iso8601] $remote_addr "$request_method $request_uri" $status ${request_time}s cipher="$ssl_cipher" received=$request_length sent=$bytes_sent';
    access_log /var/log/nginx/access.log journal_plain;
    error_log /var/log/nginx/error.log warn;

    include /etc/nginx/sites-enabled/*;
}
EOF
}

case "$MODE" in
    der)   render_der ;;
    webui) render_webui ;;
    http)  render_http ;;
    juice) render_juice ;;
esac
