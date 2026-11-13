#!/usr/bin/env python3
"""Credential-free checks for disposable release containers, never live profiles."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import urllib.request

ROOT = Path("/opt/hermes-agent")
COMMIT = "3b421578588243f920886e6fe79a6faa87ea026e"
VERSION = "0.23.1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(*args: str) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


def offline() -> None:
    require(sys.version_info[:2] == (3, 14), f"expected Python 3.14, got {sys.version}")
    require(sqlite3.sqlite_version_info >= (3, 51, 3), "SQLite must be >=3.51.3")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')")
        db.execute("INSERT INTO docs VALUES ('hermes runtime')")
        require(db.execute("SELECT count(*) FROM docs WHERE docs MATCH 'erm'").fetchone()[0] == 1,
                "loaded SQLite FTS5 trigram search failed")
    for module in ("openai", "PIL.Image", "starlette", "uvicorn", "jinja2", "multipart", "httpx", "websockets"):
        importlib.import_module(module)

    stamp = json.loads((ROOT / "install-stamp.json").read_text())
    for key, expected in {"baseVersion": VERSION, "displayVersion": VERSION, "commit": COMMIT,
                          "distribution": "docker", "updateMechanism": "external",
                          "runtimeDir": "tools"}.items():
        require(stamp.get(key) == expected, f"install stamp {key}: {stamp.get(key)!r} != {expected!r}")
    require(stamp.get("schemaVersion") == 2, "expected canonical schema-2 stamp")
    require(stamp.get("distance") == 0, "release stamp must have distance zero")
    marker = json.loads(Path("/etc/hermes/image-provenance.json").read_text())
    for key, expected in {"version": VERSION, "revision": COMMIT, "deployment_kind": "image",
                          "manager": "docker"}.items():
        require(marker.get(key) == expected, f"image provenance {key} mismatch")

    from hermes_cli.version_info import get_version_info
    actual = get_version_info()
    require(actual.display_version == VERSION, f"runtime version mismatch: {actual}")
    require(actual.commit == COMMIT, f"runtime commit mismatch: {actual}")
    for artifact in (ROOT / "hermes_cli/web_dist/index.html", ROOT / "ui-tui/dist/entry.js"):
        require(artifact.is_file() and artifact.stat().st_size > 0, f"missing prebuilt artifact: {artifact}")
    require(Path("/opt/hermes-agent/ui-tui") == Path(os.environ.get("HERMES_TUI_DIR", "")),
            "HERMES_TUI_DIR must point at prebuilt TUI")
    cli_version = command("hermes", "--version")
    require(re.search(r"(?<![\d.])v?0\.23\.1(?![\d.])", cli_version) is not None,
            f"CLI version mismatch: {cli_version}")
    gateway_help = command("hermes", "gateway", "run", "--help")
    require("--external-supervisor" in gateway_help, "gateway external supervisor flag missing")
    node_version = command("node", "--version").strip()
    require(int(node_version.lstrip("v").split(".")[0]) >= 26, f"Node >=26 required: {node_version}")
    print(command(sys.executable, "/release-tests/native_config_probe.py"), end="")
    print(json.dumps({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                      "version": actual.display_version, "commit": actual.commit, "node": node_version,
                      "offline": "passed"}, sort_keys=True))


def fetch(url: str) -> tuple[str, str]:
    # Explicitly disable proxies: only the disposable container's loopback is used.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        require(response.status == 200, f"{url}: HTTP {response.status}")
        return response.read(2_000_000).decode("utf-8"), response.headers.get("Content-Type", "")


def readiness() -> None:
    health, _ = fetch("http://127.0.0.1:8080/health")
    require(json.loads(health).get("status") == "ok", "wrapper health is not ok")
    login, kind = fetch("http://127.0.0.1:8080/login")
    require("text/html" in kind and 'type="password"' in login, "wrapper login form is not ready")
    dashboard, kind = fetch("http://127.0.0.1:9119/")
    require("text/html" in kind and "<script" in dashboard.lower() and "<html" in dashboard.lower(),
            "native dashboard has not served its built HTML application")
    # Also retrieve a built script: a fallback/error page alone is insufficient.
    scripts = re.findall(r'<script[^>]+src=[\"\']([^\"\']+)', dashboard, re.IGNORECASE)
    local_scripts = [src for src in scripts if src.startswith("/") and not src.startswith("//")]
    require(bool(local_scripts), "native dashboard has no local built script")
    script, _ = fetch("http://127.0.0.1:9119" + local_scripts[0])
    require(bool(script.strip()) and not script.lstrip().lower().startswith("<!doctype html"),
            "native dashboard script returned empty content or HTML fallback")
    print("wrapper /health, /login, native dashboard HTML and built script: ready")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("offline", "readiness"))
    args = parser.parse_args()
    if args.mode == "offline":
        offline()
    else:
        readiness()
