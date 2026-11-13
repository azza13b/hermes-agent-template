FROM debian:bookworm-slim AS sqlite-builder

# Build a WAL-reset-safe SQLite release and verify the upstream archive before
# compiling it. Python's stdlib sqlite3 extension dynamically links libsqlite3,
# so installing this shared library into /usr/local in the runtime stage makes
# Hermes use the fixed release without replacing Python itself.
ARG SQLITE_AUTOCONF_VERSION=3510300
ARG SQLITE_SHA256=81f5be397049b0cae1b167f2225af7646fc0f82e4a9b3c48c9ea3a533e21d77a
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl && \
    curl -fsSLo /tmp/sqlite.tar.gz \
      https://www.sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz && \
    echo "${SQLITE_SHA256}  /tmp/sqlite.tar.gz" | sha256sum -c - && \
    mkdir -p /tmp/sqlite-src && \
    tar -xzf /tmp/sqlite.tar.gz -C /tmp/sqlite-src --strip-components=1 && \
    cd /tmp/sqlite-src && \
    ./configure --prefix=/opt/sqlite --enable-shared --disable-static \
      --fts3 --fts4 --fts5 --rtree --session --dbstat && \
    make -j"$(nproc)" && \
    make install

FROM rust:1.97-bookworm AS google-ads-mcp-builder

# Build the Google Ads MCP once in the image instead of relying on an
# ephemeral runtime compiler. Pin the source revision for reproducible builds.
ARG GOOGLE_ADS_MCP_REF=a3d2411606cf52a7e890e4740dc2c77707e64ee5
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git && \
    git clone https://github.com/FGRibreau/mcp-google-ads.git /src/mcp-google-ads && \
    cd /src/mcp-google-ads && \
    git checkout ${GOOGLE_ADS_MCP_REF} && \
    cargo build --release && \
    strip target/release/mcp-google-ads && \
    rm -rf /var/lib/apt/lists/*

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS sqlite-runtime

# Verify the library Python actually loads, not just a sqlite CLI.
COPY --from=sqlite-builder /opt/sqlite/ /usr/local/
ENV LD_LIBRARY_PATH=/usr/local/lib
RUN ldconfig && \
    python -c "import sqlite3; print(sqlite3.sqlite_version); assert sqlite3.sqlite_version_info >= (3, 51, 3); assert sqlite3.connect(':memory:').execute(\"select sqlite_compileoption_used('ENABLE_FTS5')\").fetchone()[0] == 1"

FROM sqlite-runtime AS hermes-runtime

# Both selectors must match; overrides cannot quietly mislabel another revision.
ARG HERMES_REF=v0.23.1
ARG HERMES_COMMIT=3b421578588243f920886e6fe79a6faa87ea026e
ENV HERMES_REF=${HERMES_REF}
ENV CC=gcc CXX=g++
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV HERMES_RUNTIME_DIR=/opt/hermes-agent/tools

# Keep real tini and the template supervisor. Node/Python/pnpm come from PM,
# never a second NodeSource/npm authority. gh supports GitHub skill operations.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates git tini gh \
      build-essential libffi-dev libatomic1 xz-utils && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${HERMES_REF} https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent && \
    cd /opt/hermes-agent && \
    test "$(git rev-parse HEAD)" = "${HERMES_COMMIT}"

COPY requirements.txt /app/requirements.txt
COPY scripts/build_hermes.py /app/scripts/build_hermes.py
RUN python /app/scripts/build_hermes.py --root /opt/hermes-agent \
      --requirements /app/requirements.txt --commit ${HERMES_COMMIT} \
      --provenance /etc/hermes/image-provenance.json

# start.sh invokes python: wrapper and agent must use the SAME prepared env.
ENV PATH=/opt/hermes-agent/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
ENV HERMES_PYTHON=/opt/hermes-agent/.venv/bin/python
ENV HERMES_TUI_DIR=/opt/hermes-agent/ui-tui
ENV HERMES_WEB_DIST=/opt/hermes-agent/hermes_cli/web_dist

# start.sh copies this immutable binary into the established persistent path.
COPY --from=google-ads-mcp-builder /src/mcp-google-ads/target/release/mcp-google-ads /usr/local/bin/mcp-google-ads
RUN mkdir -p /data/.hermes
COPY server.py /app/server.py
COPY templates/ /app/templates/
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
ENV HOME=/data
ENV HERMES_HOME=/data/.hermes
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/app/start.sh"]
