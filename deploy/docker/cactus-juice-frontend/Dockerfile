# cactus-juice frontend image - built from THIS repository (context = repo root), eg:
#   podman build -f deploy/docker/cactus-juice-frontend/Dockerfile .
#
# Static SPA only - the host nginx routes /api/* to the cactus-juice API container and everything else here.

# BUILD stage
FROM docker.io/library/node:22-bookworm-slim AS builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# RUN stage - unprivileged nginx listens on 8080 and logs to stdout/stderr (-> journald)
FROM docker.io/nginxinc/nginx-unprivileged:stable-alpine

COPY deploy/docker/cactus-juice-frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
