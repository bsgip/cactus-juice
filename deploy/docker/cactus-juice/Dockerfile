# cactus-juice backend image - built from THIS repository (context = repo root), eg:
#   podman build -f deploy/docker/cactus-juice/Dockerfile .
#
# The one image is run in several "modes" (see server/update-juice.sh):
#   * API (default CMD):  uvicorn cactus_juice.main:create_app
#   * Worker task:        juice task <csipausclient|satecclient|trocaclient>
#   * DB migration:       alembic upgrade head
#
# UV Docker best practices: https://docs.astral.sh/uv/guides/integration/docker/
# BUILD stage
FROM docker.io/library/python:3.13-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/uv

# pre-compile .pyc at build time for faster startup; copy instead of hardlink (cache mount
# requirement); use the image's Python rather than downloading a new one
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first (own layer) so source-only changes don't reinstall everything
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable --extra migrate

COPY pyproject.toml uv.lock setup.cfg ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra migrate

# RUN stage
FROM docker.io/library/python:3.13-slim-bookworm

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser alembic ./alembic

USER appuser

# conf
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# API mode by default. Only ever published on 127.0.0.1 behind the host nginx, which sets X-Forwarded-*
CMD ["uvicorn", "cactus_juice.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
