#!/usr/bin/env bash
# Accept an already built image; never mount a user's /data or pass host secrets.
set -euo pipefail
IMAGE="${1:-hermes-template:v0.23.1-test}"
TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CID=""
cleanup() {
  if [[ -n "$CID" ]]; then
    docker rm -fv "$CID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
# No entrypoint/startup scripts, network, host profile, or writable source mount.
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,exec --tmpfs /data:rw,exec \
  --env HOME=/data --env HERMES_HOME=/data/.hermes \
  --env ADMIN_USERNAME=ci-fake-admin --env ADMIN_PASSWORD=CI-FAKE-NOT-A-REAL-PASSWORD \
  --mount "type=bind,src=${TEST_DIR},dst=/release-tests,readonly" \
  --entrypoint /opt/hermes-agent/.venv/bin/python \
  "$IMAGE" /release-tests/runtime_image_probe.py offline

# Phase 2 reproduces a public-URL Railway deployment with a fresh home and no
# provider keys, bootstrap auth, persistent volume, or gateway-start request.
# Never supply a native session token here: the wrapper must provision its own
# private subprocess credential, or upstream correctly refuses this public bind.
CID="$(docker run --detach --network none \
  --env PORT=8080 --env HOME=/data --env HERMES_HOME=/data/.hermes \
  --env HERMES_DASHBOARD_PUBLIC_URL=https://ci-hermes.invalid \
  --env ADMIN_USERNAME=ci-fake-admin --env ADMIN_PASSWORD=CI-FAKE-NOT-A-REAL-PASSWORD \
  --mount "type=bind,src=${TEST_DIR},dst=/release-tests,readonly" \
  "$IMAGE")"
# Every retry performs the full public-URL regression: wrapper cookie login,
# native auth protection/identity, built HTML/JS, and WebSocket admission.
# A wrapper-only /health or a public native HTML shell is never sufficient.
for attempt in $(seq 1 90); do
  if docker exec "$CID" /opt/hermes-agent/.venv/bin/python \
      /release-tests/runtime_image_probe.py readiness; then
    printf '%s\n' 'Full image smoke verification passed (no provider or gateway calls).'
    exit 0
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$CID")" != true ]]; then
    printf '%s\n' 'Wrapper container exited before readiness.' >&2
    docker logs --tail 100 "$CID" >&2
    exit 1
  fi
  sleep 2
done
printf '%s\n' 'Timed out waiting for wrapper AND native dashboard readiness.' >&2
docker logs --tail 100 "$CID" >&2
exit 1
