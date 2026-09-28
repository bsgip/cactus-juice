#!/bin/bash
# Build + deploy/update the cactus-juice containers from this repository checkout:
#   * cactus-juice-api            FastAPI backend (127.0.0.1:JUICE_API_PORT)
#   * cactus-juice-frontend       Static SPA (127.0.0.1:JUICE_FRONTEND_PORT)
#   * cactus-juice-<task>         One per JUICE_TASKS entry (`juice task <task>`)
# DB migrations (alembic upgrade head) are applied before any container is replaced.
# All containers log to journald (journalctl CONTAINER_NAME=cactus-juice-api etc).
# Run as root, after setup-juice.sh.
# Usage: sudo ./update-juice.sh ./cactus.env

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Every task runnable via `juice task <name>` (cactus_juice.tasks.TASKS) - used to clean up disabled tasks
ALL_TASKS=(csipausclient satecclient trocaclient)

required_vars=(
    JUICE_NETWORK JUICE_API_PORT JUICE_FRONTEND_PORT
    JUICE_DB_HOST JUICE_DB_PORT JUICE_DB_NAME JUICE_DB_USER JUICE_DB_PASSWORD
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

read -r -a enabled_tasks <<< "${JUICE_TASKS:-}"
for task in "${enabled_tasks[@]}"; do
    if [[ " ${ALL_TASKS[*]} " != *" ${task} "* ]]; then
        echo "ERROR: unknown task '${task}' in JUICE_TASKS (expected one of: ${ALL_TASKS[*]})" >&2
        exit 1
    fi
done

read -r -a satec_devices <<< "${JUICE_SATEC_DEVICES:-}"
for device in "${satec_devices[@]}"; do
    if [[ ! -c "$device" ]]; then
        echo "ERROR: JUICE_SATEC_DEVICES entry '${device}' is not a character device on this host." >&2
        exit 1
    fi
done

if ! podman network exists "$JUICE_NETWORK"; then
    echo "ERROR: podman network '${JUICE_NETWORK}' does not exist - run setup-juice.sh first." >&2
    exit 1
fi

# Password is URL encoded so it can contain any character
JUICE_DB_PASSWORD_ENCODED=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
    "$JUICE_DB_PASSWORD")
JUICE_DATABASE_URL="postgresql+asyncpg://${JUICE_DB_USER}:${JUICE_DB_PASSWORD_ENCODED}@${JUICE_DB_HOST}:${JUICE_DB_PORT}/${JUICE_DB_NAME}"

# --------------------------------------------------------------------------- #
# Build images                                                                #
# --------------------------------------------------------------------------- #
# Tagged with the commit (+ "-dirty" for uncommitted changes) so `podman ps` shows what's deployed.
git_cmd=(git -c "safe.directory=${REPO_ROOT}" -C "$REPO_ROOT")
VERSION_TAG="$("${git_cmd[@]}" rev-parse --short HEAD)"
if [[ -n "$("${git_cmd[@]}" status --porcelain --untracked-files=no)" ]]; then
    VERSION_TAG="${VERSION_TAG}-dirty"
fi

JUICE_IMAGE="localhost/cactus-juice:${VERSION_TAG}"
JUICE_FRONTEND_IMAGE="localhost/cactus-juice-frontend:${VERSION_TAG}"

echo "==> Building ${JUICE_IMAGE}..."
podman build -t "$JUICE_IMAGE" -t localhost/cactus-juice:latest \
    -f "${REPO_ROOT}/deploy/docker/cactus-juice/Dockerfile" "$REPO_ROOT"

echo "==> Building ${JUICE_FRONTEND_IMAGE}..."
podman build -t "$JUICE_FRONTEND_IMAGE" -t localhost/cactus-juice-frontend:latest \
    -f "${REPO_ROOT}/deploy/docker/cactus-juice-frontend/Dockerfile" "$REPO_ROOT"

# --------------------------------------------------------------------------- #
# Database migrations                                                          #
# --------------------------------------------------------------------------- #
echo "==> Applying database migrations..."
podman run --rm \
    --network "$JUICE_NETWORK" \
    -e JUICE_DATABASE_URL="$JUICE_DATABASE_URL" \
    "$JUICE_IMAGE" \
    alembic upgrade head

# --------------------------------------------------------------------------- #
# Containers                                                                  #
# --------------------------------------------------------------------------- #
# (Re)creates a container named $1 (also its journald tag), passing all remaining args to podman run
deploy_container() {
    local name="$1"
    shift
    echo "==> Deploying ${name}..."
    podman rm -f "$name" >/dev/null 2>&1 || true
    podman run -d \
        --name "$name" \
        --restart always \
        --network "$JUICE_NETWORK" \
        --log-driver=journald \
        --log-opt=tag="$name" \
        "$@"
}

deploy_container cactus-juice-api \
    -p "127.0.0.1:${JUICE_API_PORT}:8080" \
    -e JUICE_DATABASE_URL="$JUICE_DATABASE_URL" \
    "$JUICE_IMAGE"

deploy_container cactus-juice-frontend \
    -p "127.0.0.1:${JUICE_FRONTEND_PORT}:8080" \
    "$JUICE_FRONTEND_IMAGE"

for task in "${ALL_TASKS[@]}"; do
    name="cactus-juice-${task}"
    if [[ " ${enabled_tasks[*]} " != *" ${task} "* ]]; then
        if podman container exists "$name"; then
            echo "==> Removing disabled task container ${name}..."
            podman rm -f "$name"
        fi
        continue
    fi

    task_args=()
    case "$task" in
        csipausclient)
            task_args+=(-e JUICE_CSIPAUS_RESOLVE_OVERRIDES="${JUICE_CSIPAUS_RESOLVE_OVERRIDES:-"{}"}")
            ;;
        satecclient)
            for device in "${satec_devices[@]}"; do
                task_args+=(--device "${device}:${device}")
            done
            # Serial devices are typically root:dialout 660 and the image runs as a non-root user
            if (( ${#satec_devices[@]} > 0 )) && getent group dialout >/dev/null 2>&1; then
                task_args+=(--group-add "$(getent group dialout | cut -d: -f3)")
            fi
            ;;
    esac

    deploy_container "$name" \
        -e JUICE_DATABASE_URL="$JUICE_DATABASE_URL" \
        "${task_args[@]}" \
        "$JUICE_IMAGE" \
        juice task "$task"
done

echo ""
echo "==> Update complete (${VERSION_TAG}). Running containers:"
podman ps --filter name=cactus-juice
echo ""
echo "Logs: journalctl -f CONTAINER_NAME=cactus-juice-api  (or any other container name above)"
echo "Old image tags are not removed automatically - see README.md for pruning."
