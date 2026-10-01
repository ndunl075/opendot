# OpenDot daemon for a home server or small VPS (deploy/README.md).
# Build from the repository root:  docker build -f deploy/Dockerfile -t opendot .
# The daemon listens on 127.0.0.1 inside the container (see deploy/compose.yaml, which shares the
# host's loopback so nothing is published on a network interface) and runs as an unprivileged user.

FROM node:22-slim AS ui
WORKDIR /src/ui
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY ui ./
COPY contract /src/contract
RUN pnpm build

FROM python:3.13-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENDOT_DB_PATH=/data/opendot.db \
    OPENDOT_UI_DIST=/app/ui
COPY daemon /tmp/daemon
RUN pip install /tmp/daemon && rm -rf /tmp/daemon
COPY --from=ui /src/ui/dist /app/ui
RUN useradd --system --uid 10001 --create-home --home-dir /home/opendot opendot \
    && mkdir -p /data && chown opendot:opendot /data
USER opendot
WORKDIR /data
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 3)"]
ENTRYPOINT ["opendot"]
CMD ["serve", "--port", "8765", "--token-file", "/run/secrets/opendot-token"]
