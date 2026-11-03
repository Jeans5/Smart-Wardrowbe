# Smart Wardrowbe as one Home Assistant app: PostgreSQL, Redis, the FastAPI backend, both arq
# workers and the Next.js frontend in a single container, started by run.sh.
#
# Home Assistant builds an app from this folder alone, so the code is cloned from the repo.
# BUILD_VERSION (passed by the Supervisor) is declared before the clone so that every new app
# version re-clones instead of reusing a cached, older checkout.

FROM alpine/git:latest AS source
ARG BUILD_VERSION=dev
ARG WARDROWBE_REPO=https://github.com/Jeans5/Smart-Wardrowbe.git
ARG WARDROWBE_REF=main
RUN echo "Cloning ${WARDROWBE_REPO}@${WARDROWBE_REF} for app version ${BUILD_VERSION}" \
    && git clone --depth 1 --branch "${WARDROWBE_REF}" "${WARDROWBE_REPO}" /src

# Built on Debian (glibc) rather than Alpine, because the standalone output carries native
# modules (sharp) that must match the Debian runtime below.
FROM node:24-bookworm-slim AS frontend
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=source /src/frontend/package.json /src/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=source /src/frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm

# Debian would create a default cluster while installing PostgreSQL; run.sh makes its own in /data.
RUN mkdir -p /etc/postgresql-common \
    && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.conf \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        gosu \
        jq \
        libpq-dev \
        libstdc++6 \
        postgresql-15 \
        redis-server \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=source /src/backend/requirements.txt /src/backend/requirements-extras.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-extras.txt

# Background-removal model and numba cache baked in, as in the upstream backend image.
ENV REMBG_HOME=/opt/rembg \
    NUMBA_CACHE_DIR=/opt/numba_cache \
    NUMBA_CPU_NAME=generic
RUN python -c "from rembg import new_session; new_session('u2net')"

COPY --from=source /src/backend/ /app/

COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /app/.next/standalone /frontend/
COPY --from=frontend /app/.next/static /frontend/.next/static
COPY --from=frontend /app/public /frontend/public
COPY --from=frontend /app/messages /frontend/messages

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
