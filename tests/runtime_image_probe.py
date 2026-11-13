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


def checked_json(response):
    # Never include bodies/headers in failures: auth responses may contain secrets.
    require(response.status_code == 200,
            f"{response.request.url.path}: expected JSON HTTP 200, got {response.status_code}")
    require("application/json" in response.headers.get("content-type", ""),
            f"{response.request.url.path}: expected JSON, not a login/SPA fallback")
    data = response.json()
    require(isinstance(data, dict), f"{response.request.url.path}: expected JSON object")
    return data


def websocket_readiness(cookie: str) -> None:
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect
    from websockets.typing import Origin

    # /api/ws is the in-process JSON-RPC sidecar, not /api/pty (which spawns a
    # CLI) or a gateway start. No RPC, chat, provider, or model request is sent.
    for port in (9119, 8080):
        try:
            with connect(f"ws://127.0.0.1:{port}/api/ws", proxy=None, open_timeout=5):
                raise RuntimeError(f"unauthenticated /api/ws on {port} accepted")
        except InvalidStatus as exc:
            require(exc.response.status_code in (401, 403),
                    f"unauthenticated /api/ws on {port}: expected auth rejection")
    try:
        with connect("ws://127.0.0.1:8080/api/ws", proxy=None, origin=Origin("https://attacker.invalid"),
                     additional_headers={"Cookie": cookie}, open_timeout=5):
            raise RuntimeError("cross-origin browser WebSocket accepted")
    except InvalidStatus as exc:
        require(exc.response.status_code == 403, "cross-origin WebSocket must be rejected")
    with connect("ws://127.0.0.1:8080/api/ws", proxy=None, origin=Origin("http://127.0.0.1:8080"),
                 additional_headers={"Cookie": cookie}, open_timeout=15) as ws:
        # A completed upgrade plus a real pong proves both wrapper and upstream
        # admission; accepting only the outer socket and then closing won't pass.
        require(ws.ping().wait(timeout=5), "authenticated native /api/ws did not pong")


def readiness() -> None:
    import httpx

    require(os.environ.get("HERMES_DASHBOARD_PUBLIC_URL") == "https://ci-hermes.invalid",
            "public-URL regression must run with the external CI URL")
    require(not os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN"),
            "test harness must not provision the native auth credential")
    wrapper = "http://127.0.0.1:8080"
    native = "http://127.0.0.1:9119"
    # Explicitly disable proxies and redirects; a login page is not API success.
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as browser:
        require(checked_json(browser.get(wrapper + "/health")).get("status") == "ok",
                "wrapper health is not ok")
        logged_out = browser.get(wrapper + "/", headers={"Accept": "text/html"})
        require(logged_out.status_code == 302
                and logged_out.headers.get("location", "").startswith("/login"),
                "logged-out wrapper root must redirect to login")
        login = browser.get(wrapper + "/login")
        require(login.status_code == 200 and "text/html" in login.headers.get("content-type", "")
                and 'type="password"' in login.text, "wrapper login form is not ready")

        health = checked_json(browser.get(native + "/api/health"))
        require(health.get("auth_required") is True,
                "public-URL native listener must have auth_required=true")
        # HTML and /api/status are public shells/liveness in canonical upstream;
        # neither proves protection. /api/config really is behind its auth gate.
        denied = browser.get(native + "/api/config")
        require(denied.status_code in (401, 403),
                "direct native /api/config without credentials must be denied")

        logged_in = browser.post(wrapper + "/login", data={
            "username": "ci-fake-admin", "password": "CI-FAKE-NOT-A-REAL-PASSWORD",
            "returnTo": "/?force=1",
        })
        require(logged_in.status_code == 302
                and logged_in.headers.get("location") == "/?force=1"
                and bool(browser.cookies), "fake-admin wrapper form login failed")
        cookie = "; ".join(f"{item.name}={item.value}" for item in browser.cookies.jar)
        # With no provider configured, force=1 deliberately bypasses the wrapper's
        # first-visit /setup redirect, not either authentication gate.
        dashboard = browser.get(wrapper + "/?force=1")
        require(dashboard.status_code == 200
                and "text/html" in dashboard.headers.get("content-type", "")
                and "<script" in dashboard.text.lower() and "<html" in dashboard.text.lower(),
                "authenticated wrapper did not serve native built HTML")
        scripts = re.findall(r'<script[^>]+src=[\"\']([^\"\']+)', dashboard.text, re.IGNORECASE)
        local_scripts = [src for src in scripts if src.startswith("/") and not src.startswith("//")]
        require(bool(local_scripts), "native dashboard has no local built script")
        script = browser.get(wrapper + local_scripts[0])
        require(script.status_code == 200 and bool(script.text.strip())
                and "text/html" not in script.headers.get("content-type", "")
                and not script.text.lstrip().lower().startswith("<!doctype html"),
                "authenticated built script returned an error or HTML fallback")

        config = checked_json(browser.get(wrapper + "/api/config"))
        require("model" in config and "agent" in config,
                "authenticated native config response lacks canonical config fields")
        identity = checked_json(browser.get(wrapper + "/api/auth/me"))
        # Canonical /api/auth/me has no signed_in boolean: 200 plus this verified
        # Session identity is what the SPA uses to avoid a second sign-in screen.
        require(bool(identity.get("user_id")) and identity.get("provider") == "session-token",
                "wrapper cookie did not establish a native authenticated session identity")
        websocket_readiness(cookie)
        status = checked_json(browser.get(wrapper + "/api/status"))
        require(status.get("auth_required") is True, "native auth gate was disabled")
        require(status.get("gateway_running") is False, "smoke test must not start a gateway")
        require(browser.post(wrapper + "/api/model/set", json={},
                             headers={"Origin": "https://attacker.invalid"}).status_code == 403,
                "cross-origin browser write must not receive native credentials")
        signed_out = browser.post(wrapper + "/auth/logout", headers={"Origin": wrapper})
        require(signed_out.status_code == 302, "native logout did not redirect to login")
        require(browser.get(wrapper + "/api/config").status_code == 401,
                "native logout must revoke the wrapper login cookie")
    print("public-URL native auth gate, wrapper login, protected config/identity, "
          "built HTML/JS and authenticated WebSocket: ready (gateway stopped)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("offline", "readiness"))
    args = parser.parse_args()
    if args.mode == "offline":
        offline()
    else:
        readiness()
