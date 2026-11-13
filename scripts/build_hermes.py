#!/usr/bin/env python3
"""Build the custom image using v0.23.1's canonical, sealed PM pipeline.

This helper runs only while building the image; it never reads a live profile.
The bootstrap interpreter is not the agent interpreter. PM owns runtime tool pins.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys


def validate_source(root: Path, commit: str) -> None:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if actual != commit:
        raise ValueError(f"checkout {actual} does not match admitted commit {commit}")


def write_provenance(root: Path, marker: Path) -> None:
    path = root / "install-stamp.json"
    stamp = json.loads(path.read_text())
    stamp["pmRuntime"] = str(root / "pm-runtime")
    path.write_text(json.dumps(stamp, indent=2) + "\n")
    (root / ".install_method").write_text("docker\n")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"schema": 1, "deployment_kind": "image", "manager": "docker",
                                  "image": "hermes-railway-template", "version": stamp["displayVersion"],
                                  "revision": stamp["commit"]}, sort_keys=True) + "\n")
    marker.chmod(0o444)

EXTRAS = ("all", "messaging", "tts-premium", "bedrock", "anthropic", "edge-tts", "documents")


def build_commands(root: Path, python: Path, uv: Path, requirements: Path, commit: str) -> list[list[str]]:
    """Explicit argv, in dependency order; no shell, npm install, or live state."""
    venv = str(root / ".venv/bin/python")
    constraints = str(root / "wrapper-constraints.txt")
    products = Path("/tmp/hermes-products")
    icons = "/tmp/hermes-icons"
    environment = [str(python), "-m", "pm.build_env", "--source", str(root),
                   "--python", str(python), "--out", str(root / ".venv"),
                   "--cache", str(root / "uv-cache"), "--sealed", "--record-selection", "--no-install-project"]
    for extra in EXTRAS:
        environment += ["--extra", extra]
    return [
        environment,
        [str(python), "-c",
         "from pathlib import Path; from pm import stage_manager_runtime; "
         "from scripts.bundles.payload import seal_pm_runtime; "
         f"root=Path({str(root)!r}); python=Path({str(python)!r}); "
         "stage_manager_runtime(python=python, destination=root/'pm-runtime', project=root/'pm'); "
         "seal_pm_runtime(root, python)"],
        # Freeze EVERY existing dependency before the wrapper's broad lower bounds
        # resolve. Incompatible wrapper changes fail rather than upgrading the agent.
        [venv, "-I", "-c", "from importlib.metadata import distributions; from pathlib import Path; "
         f"Path({constraints!r}).write_text(''.join(sorted("
         "f'{d.metadata[\"Name\"]}=={d.version}\\n' for d in distributions())), encoding='utf-8')"],
        [str(uv), "pip", "install", "--python", venv, "--constraint", constraints,
         "--requirements", str(requirements)],
        [str(python), "-c", "import subprocess,sys; from pathlib import Path; "
         "from pm.pip_check import unexpected_incompatibilities; "
         f"r=subprocess.run({[str(uv), 'pip', 'check', '--python', venv]!r}, capture_output=True, text=True); "
         "report=r.stdout+r.stderr; print(report, end=''); "
         f"sys.exit(r.returncode if r.returncode and unexpected_incompatibilities(report, Path({str(root / 'uv.lock')!r})) != [] else 0)"],
        ["node", "scripts/build/node-deps.mjs", "--source", str(root), "--workspace", "ui-tui", "--workspace", "web"],
        [venv, "-I", "scripts/generate_icons.py", "--source", str(root), "--out", icons],
        ["node", "scripts/build/tui.mjs", "--source", str(root), "--out", str(products / "tui")],
        ["node", "scripts/build/web.mjs", "--source", str(root), "--out", str(products / "web"), "--icons", icons],
        [venv, "scripts/write_install_stamp.py", "--output", str(root / "install-stamp.json"),
         "--commit", commit, "--base-version", "0.23.1", "--distance", "0", "--distribution", "docker",
         "--source", "docker", "--update-mechanism", "external", "--runtime-dir", "tools"],
        [venv, "-I", "-c", "import shutil; from pathlib import Path; "
         f"root=Path({str(root)!r}); products=Path({str(products)!r}); "
         "shutil.rmtree(root/'ui-tui'); shutil.copytree(products/'tui', root/'ui-tui'); "
         "shutil.copytree(products/'web', root/'hermes_cli/web_dist', dirs_exist_ok=True)"],
        [venv, "-c", "from pathlib import Path; from docker.build_agent import assemble_image; "
         f"assemble_image(Path({str(root)!r}))"],
    ]


def provision_tools(root: Path, *, bindir: Path = Path("/usr/local/bin")) -> tuple[Path, Path]:
    # The source tree's PM is stdlib-bootstrappable and verifies archive hashes.
    sys.path.insert(0, str(root))
    from pm import ensure, env_for, installed_package, uv_launcher

    lock = json.loads((root / "pm/lock.json").read_text())["packages"]
    if int(lock["node"]["version"].split(".")[0]) < 26:
        raise ValueError("the release requires Node >=26")
    for name in ("python", "uv", "node", "pnpm", "npm"):
        ensure(name, explicit=True)
    bindir.mkdir(parents=True, exist_ok=True)
    for name in ("node", "pnpm", "npm"):
        link = bindir / name
        link.unlink(missing_ok=True)
        link.symlink_to(installed_package(name).binary)
    # Preserve npm/npx for ordinary website and CLI work, not just pnpm builds.
    npx = shutil.which("npx", path=env_for("npm", base_env={})["PATH"])
    if not npx:
        raise RuntimeError("PM's npm package did not expose npx")
    npx_link = bindir / "npx"
    npx_link.unlink(missing_ok=True)
    npx_link.symlink_to(npx)
    # uv is not an application package: PM exposes a separate read-only lookup.
    # Only the wrapper overlay uses it; Hermes dependency builds go through PM.
    uv = uv_launcher("uv")
    if uv is None:
        raise RuntimeError("PM did not stage its pinned uv launcher")
    return installed_package("python").binary.resolve(), uv.resolve()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.commit != "3b421578588243f920886e6fe79a6faa87ea026e":
        parser.error("this pipeline admits only the v0.23.1 release commit")
    root = args.root.resolve()
    validate_source(root, args.commit)
    # Deliberately independent from any volume or host profile during build.
    os.environ["HERMES_RUNTIME_DIR"] = str(root / "tools")
    os.environ["HERMES_HOME"] = "/tmp/hermes-build-home"
    python, uv = provision_tools(root)
    sqlite_gate = ("import sqlite3,sys; assert sys.version_info[:2] == (3,14); "
                   "assert sqlite3.sqlite_version_info >= (3,51,3); "
                   "db=sqlite3.connect(':memory:'); "
                   "db.execute('CREATE VIRTUAL TABLE docs USING fts5(content, tokenize=trigram)'); "
                   "db.execute(\"INSERT INTO docs VALUES ('hermes')\"); "
                   "assert db.execute(\"SELECT count(*) FROM docs WHERE docs MATCH 'erm'\").fetchone()[0] == 1")
    subprocess.run([str(python), "-I", "-c", sqlite_gate], cwd=root, check=True)
    for command in build_commands(root, python, uv, args.requirements, args.commit):
        subprocess.run(command, cwd=root, check=True)
    write_provenance(root, args.provenance)
    # Native ownership detection treats any .git directory as a mutable source
    # checkout. Remove it only after validating and stamping the release image.
    shutil.rmtree(root / ".git")
    # Check the actual assembled interpreter too, after source binding.
    subprocess.run([str(root / ".venv/bin/python"), "-I", "-c", sqlite_gate], cwd=root, check=True)


if __name__ == "__main__":
    main()
