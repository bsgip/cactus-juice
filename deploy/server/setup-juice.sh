#!/bin/bash
# One-shot (re-runnable) host setup for cactus-juice. Run AFTER setup.sh (requires podman).
#   * Creates the juice podman network
#   * Creates/updates the juice postgres role + database on the host's native postgres (+ pg_hba entry)
#   * Writes the nginx basic auth file for JUICE_FQDN
# Run as root.
# Usage: sudo ./setup-juice.sh ./cactus.env

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Error: this script must be run as root (try: sudo $0)" >&2
    exit 1
fi

ENV_FILE="${1:-./cactus.env}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: env file not found: $ENV_FILE"
    echo "Usage: sudo $0 <path-to-cactus.env>"
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

required_vars=(
    JUICE_NETWORK JUICE_NETWORK_SUBNET JUICE_NETWORK_GATEWAY
    JUICE_DB_NAME JUICE_DB_USER JUICE_DB_PASSWORD
    JUICE_BASIC_AUTH_USER JUICE_BASIC_AUTH_PASSWORD JUICE_HTPASSWD_PATH
)
missing_vars=()
for var_name in "${required_vars[@]}"; do
    [[ -n "${!var_name:-}" ]] || missing_vars+=("$var_name")
done
if (( ${#missing_vars[@]} > 0 )); then
    echo "Error: the following required variables are not set or empty in ${ENV_FILE}:" >&2
    printf '  - %s\n' "${missing_vars[@]}" >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# Podman network                                                              #
# --------------------------------------------------------------------------- #
echo "==> Creating ${JUICE_NETWORK} (${JUICE_NETWORK_SUBNET}, gateway ${JUICE_NETWORK_GATEWAY})..."
if podman network exists "$JUICE_NETWORK"; then
    actual_subnet=$(podman network inspect "$JUICE_NETWORK" --format '{{range .Subnets}}{{.Subnet}} {{end}}')
    echo "    Already exists (subnet: ${actual_subnet}), skipping."
    if [[ " ${actual_subnet} " != *" ${JUICE_NETWORK_SUBNET} "* ]]; then
        echo "    WARNING: existing subnet does not match JUICE_NETWORK_SUBNET - remove the network (after removing"
        echo "    the juice containers) to recreate it: podman network rm ${JUICE_NETWORK}"
    fi
else
    podman network create --subnet "$JUICE_NETWORK_SUBNET" --gateway "$JUICE_NETWORK_GATEWAY" "$JUICE_NETWORK"
fi

# --------------------------------------------------------------------------- #
# Postgres (native, on this host)                                             #
# --------------------------------------------------------------------------- #
echo "==> Configuring postgres role '${JUICE_DB_USER}' / database '${JUICE_DB_NAME}'..."

pg() {
    runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -q "$@"
}

if ! command -v psql >/dev/null 2>&1 || ! pg -tAc "select 1" >/dev/null 2>&1; then
    echo "ERROR: unable to connect to the local postgres as the 'postgres' OS user." >&2
    echo "Create the role/database manually (see README.md) and add a pg_hba.conf entry for ${JUICE_NETWORK_SUBNET}." >&2
    exit 1
fi

# psql variables (not string interpolation) so the password never needs escaping. \gexec runs the generated
# statement only if the SELECT returns a row, making the CREATEs idempotent.
pg -v db="$JUICE_DB_NAME" -v user="$JUICE_DB_USER" -v pw="$JUICE_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'user') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user') \gexec
ALTER ROLE :"user" WITH LOGIN PASSWORD :'pw';
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
ALTER DATABASE :"db" OWNER TO :"user";
SQL
echo "    Role + database are current."

HBA_FILE=$(pg -tAc "SHOW hba_file")
echo "==> Checking ${HBA_FILE} allows ${JUICE_DB_USER}@${JUICE_DB_NAME} from ${JUICE_NETWORK_SUBNET}..."
if awk -v d="$JUICE_DB_NAME" -v u="$JUICE_DB_USER" -v s="$JUICE_NETWORK_SUBNET" \
        '$1=="host" && $2==d && $3==u && $4==s {found=1} END {exit !found}' "$HBA_FILE"; then
    echo "    Entry already present, skipping."
else
    {
        echo ""
        echo "# cactus-juice containers (${JUICE_NETWORK}) - added by setup-juice.sh"
        echo "host    ${JUICE_DB_NAME}    ${JUICE_DB_USER}    ${JUICE_NETWORK_SUBNET}    scram-sha-256"
    } >> "$HBA_FILE"
    pg -tAc "SELECT pg_reload_conf()" >/dev/null
    echo "    Entry added and postgres config reloaded."
fi

LISTEN_ADDRESSES=$(pg -tAc "SHOW listen_addresses")
if [[ "$LISTEN_ADDRESSES" != "*" && ",${LISTEN_ADDRESSES// /}," != *",${JUICE_NETWORK_GATEWAY},"* ]]; then
    echo "    WARNING: postgres listen_addresses='${LISTEN_ADDRESSES}' does not include ${JUICE_NETWORK_GATEWAY}."
    echo "    The juice containers will be unable to connect. Set listen_addresses = '*' in postgresql.conf"
    echo "    (pg_hba.conf still restricts who may connect) and restart postgresql."
fi

# --------------------------------------------------------------------------- #
# nginx basic auth                                                            #
# --------------------------------------------------------------------------- #
echo "==> Writing nginx basic auth file ${JUICE_HTPASSWD_PATH}..."
mkdir -p "$(dirname "$JUICE_HTPASSWD_PATH")"
htpasswd_line="${JUICE_BASIC_AUTH_USER}:$(openssl passwd -apr1 -stdin <<< "$JUICE_BASIC_AUTH_PASSWORD")"
( umask 027; echo "$htpasswd_line" > "$JUICE_HTPASSWD_PATH" )
if getent group www-data >/dev/null 2>&1; then
    chown root:www-data "$JUICE_HTPASSWD_PATH"
fi
chmod 640 "$JUICE_HTPASSWD_PATH"

echo ""
echo "==> cactus-juice setup complete."
echo ""
echo "Next steps:"
echo "  1. Render + enable the nginx vhost: ./nginx-config.sh juice ${ENV_FILE} > /etc/nginx/sites-available/${JUICE_FQDN:-<JUICE_FQDN>}"
if [[ "${UI_TLS_MODE:-letsencrypt}" != "none" ]]; then
    echo "     and issue a certificate for ${JUICE_FQDN:-<JUICE_FQDN>} (see README.md)"
fi
echo "  2. Build + deploy the containers: ./update-juice.sh ${ENV_FILE}"
