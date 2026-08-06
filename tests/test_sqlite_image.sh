#!/usr/bin/env bash
set -euo pipefail

IMAGE="hermes-sqlite-runtime-test:${GITHUB_SHA:-local}"

docker build --target sqlite-runtime -t "$IMAGE" .

docker run --rm --entrypoint python "$IMAGE" -c \
  "import sqlite3; assert sqlite3.sqlite_version_info >= (3, 51, 3); assert sqlite3.connect(':memory:').execute(\"select sqlite_compileoption_used('ENABLE_FTS5')\").fetchone()[0] == 1; print(sqlite3.sqlite_version)"

docker run --rm --entrypoint sh "$IMAGE" -c \
  'ldd "$(python -c "import _sqlite3; print(_sqlite3.__file__)")" | grep -F "/usr/local/lib/libsqlite3.so.0"'
