#!/bin/bash
set -e

# Mirror dashboard-ref-only's startup: create every directory hermes expects
# and seed a default config.yaml if the volume is empty. Without these,
# `hermes dashboard` endpoints that hit logs/, sessions/, cron/, etc. can fail
# with opaque errors even though no auth is actually involved.
# NOTE (hermes >= v2026.7.1): several dirs were consolidated and are now
# resolved via get_hermes_dir("<new>", "<old>"), which returns the NEW path
# unless the OLD one already has *content*. Seeding an empty legacy stub no
# longer "claims" it — hermes ignores empty stubs and writes to the new path
# (upstream #27602). So we seed the NEW paths: pairing -> platforms/pairing,
# image_cache -> cache/images, audio_cache -> cache/audio. A populated legacy
# dir from a pre-v2026.7.1 deploy still wins on both sides, so no migration is
# needed. server.py:_resolve_pairing_dir() mirrors this same rule for the
# admin panel's Users tab — keep the two in sync on future bumps.
mkdir -p /data/.hermes/cron /data/.hermes/sessions /data/.hermes/logs \
         /data/.hermes/memories /data/.hermes/skills /data/.hermes/platforms/pairing \
         /data/.hermes/hooks /data/.hermes/cache/images /data/.hermes/cache/audio \
         /data/.hermes/workspace /data/.hermes/skins /data/.hermes/plans \
         /data/.hermes/home

# Install ownership belongs to the immutable code tree: the image carries
# install-stamp.json (updateMechanism=external), image provenance, and the
# legacy code-scoped docker marker. Do not rewrite the persistent home's
# marker: that volume may also be used by a host-side Hermes installation.

if [ ! -f /data/.hermes/config.yaml ] && [ -f /opt/hermes-agent/cli-config.yaml.example ]; then
  cp /opt/hermes-agent/cli-config.yaml.example /data/.hermes/config.yaml
fi

[ ! -f /data/.hermes/.env ] && touch /data/.hermes/.env

# Refresh the Google Ads MCP binary from the immutable image into a stable path
# on the persistent volume. Credentials and config remain under /data; only the
# executable is replaced when a new image intentionally pins a newer build.
if [ -x /usr/local/bin/mcp-google-ads ]; then
  mkdir -p /data/tools/google-ads-mcp/bin
  install -m 0755 /usr/local/bin/mcp-google-ads /data/tools/google-ads-mcp/bin/mcp-google-ads
fi

# Bootstrap OAuth tokens from env var (e.g. xAI Grok SuperGrok).
# Set HERMES_AUTH_JSON_BOOTSTRAP to the contents of a locally-generated
# ~/.hermes/auth.json. Written only once — subsequent token refreshes update
# the file in place on the persistent volume.
if [ ! -f /data/.hermes/auth.json ] && [ -n "${HERMES_AUTH_JSON_BOOTSTRAP}" ]; then
  printf '%s' "${HERMES_AUTH_JSON_BOOTSTRAP}" > /data/.hermes/auth.json
  chmod 600 /data/.hermes/auth.json
fi

# Clear any stale gateway PID file left over from the previous container.
# `hermes gateway` writes /data/.hermes/gateway.pid on start but does not
# remove it on SIGTERM. Since /data is a persistent volume, the file
# survives container restarts and causes every subsequent boot to exit with
# "ERROR gateway.run: PID file race lost to another gateway instance".
# No hermes process can be running at this point (we're pre-exec in a fresh
# container), so removing the file unconditionally is safe.
rm -f /data/.hermes/gateway.pid

# Tell the dashboard its externally reachable URL.
# hermes >= v2026.7.20 builds the MCP OAuth redirect_uri from the request's own
# Host header. Our reverse proxy must strip that Host (hermes 400s anything but
# loopback on a loopback bind), so hermes would otherwise hand the OAuth
# provider `http://127.0.0.1:9119/...` — a URL only reachable inside this
# container, leaving the browser on a dead tab after consent with nothing in the
# logs. resolve_public_url() checks HERMES_DASHBOARD_PUBLIC_URL first, so
# setting it is the supported fix. Railway injects RAILWAY_PUBLIC_DOMAIN; `:=`
# keeps an operator-set value (e.g. a custom domain) winning.
if [ -n "${RAILWAY_PUBLIC_DOMAIN:-}" ]; then
  : "${HERMES_DASHBOARD_PUBLIC_URL:=https://${RAILWAY_PUBLIC_DOMAIN}}"
  export HERMES_DASHBOARD_PUBLIC_URL
fi

exec python /app/server.py
