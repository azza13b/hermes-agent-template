"""Source-level release contracts; these do NOT verify a Docker image."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseDockerTests(unittest.TestCase):
    def test_custom_image_pins_supported_release_without_replacing_supervision(self):
        docker = (ROOT / "Dockerfile").read_text()
        self.assertIn("python3.14-bookworm-slim AS sqlite-runtime", docker)
        self.assertIn("ARG HERMES_REF=v0.23.1", docker)
        self.assertIn("ARG HERMES_COMMIT=3b421578588243f920886e6fe79a6faa87ea026e", docker)
        self.assertIn('test "$(git rev-parse HEAD)" = "${HERMES_COMMIT}"', docker)
        self.assertIn("scripts/build_hermes.py", docker)
        self.assertIn("git tini gh", docker)
        self.assertIn('ENTRYPOINT ["/usr/bin/tini", "-g", "--"]', docker)
        self.assertIn("ENV HOME=/data", docker)
        self.assertIn("ENV HERMES_HOME=/data/.hermes", docker)
        self.assertIn("ENV PATH=/opt/hermes-agent/.venv/bin:", docker)
        self.assertNotIn("setup_22.x", docker)
        self.assertNotIn("s6-overlay", docker)
        self.assertNotIn("uv pip install --system", docker)


class ReleaseBuildPlanTests(unittest.TestCase):
    def test_prepares_frozen_managed_environment_before_binding_products(self):
        import importlib.util
        helper = ROOT / "scripts/build_hermes.py"
        self.assertTrue(helper.is_file(), "release must have an executable build helper")
        spec = importlib.util.spec_from_file_location("release_builder", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path("/opt/hermes-agent")
        commands = module.build_commands(root, Path("/managed/python"), Path("/managed/uv"),
                                         Path("/app/requirements.txt"), "3b421578588243f920886e6fe79a6faa87ea026e")
        joined = [" ".join(map(str, command)) for command in commands]
        environment = next(c for c in commands if "pm.build_env" in c)
        for flag in ("--sealed", "--record-selection", "--no-install-project"):
            self.assertIn(flag, environment)
        extras = [environment[i + 1] for i, arg in enumerate(environment) if arg == "--extra"]
        self.assertEqual(set(extras), {"all", "messaging", "tts-premium", "bedrock", "anthropic", "edge-tts", "documents"})
        wrapper = next(c for c in commands if "pip" in c and "install" in c)
        self.assertIn("--constraint", wrapper)
        self.assertIn(str(root / ".venv/bin/python"), wrapper)
        self.assertIn(str(root / "wrapper-constraints.txt"), wrapper)
        self.assertTrue(any("unexpected_incompatibilities" in c and "uv.lock" in c for c in joined),
                        "PM's intentional Pillow override must not make the image fail pip check")
        self.assertTrue(any("stage_manager_runtime" in c and "seal_pm_runtime" in c for c in joined))
        node_deps = next(c for c in commands if "scripts/build/node-deps.mjs" in c)
        self.assertEqual(node_deps[-4:], ["--workspace", "ui-tui", "--workspace", "web"])
        icons = next(c for c in commands if "scripts/generate_icons.py" in c)
        self.assertEqual(icons[:2], [str(root / ".venv/bin/python"), "-I"])
        self.assertIn("--out", icons)
        tui = next(c for c in commands if "scripts/build/tui.mjs" in c)
        web = next(c for c in commands if "scripts/build/web.mjs" in c)
        self.assertIn("--out", tui)
        self.assertIn("--icons", web)
        stamp = next(c for c in commands if "scripts/write_install_stamp.py" in c)
        for flag, value in (("--base-version", "0.23.1"), ("--distance", "0"),
                            ("--distribution", "docker"), ("--source", "docker"),
                            ("--update-mechanism", "external"), ("--runtime-dir", "tools")):
            self.assertEqual(stamp[stamp.index(flag) + 1], value)
        self.assertIn("assemble_image", joined[-1])
        self.assertLess(joined.index(" ".join(web)), len(joined) - 1)
    def test_validates_checkout_before_work_and_writes_immutable_provenance(self):
        import importlib.util
        import json
        import subprocess
        import tempfile
        helper = ROOT / "scripts/build_hermes.py"
        spec = importlib.util.spec_from_file_location("release_builder", helper)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "validate_source"), "reject mismatched checkout before installing")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "pyproject.toml").write_text('[project]\nversion="0.23.1"\n')
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            module.validate_source(root, commit)
            # Upstream intentionally keeps PEP 621 version at 0.0.0; the
            # admitted release stamp, not this development field, is identity.
            (root / "pyproject.toml").write_text('[project]\nversion="0.0.0"\n')
            module.validate_source(root, commit)
            with self.assertRaises(ValueError):
                module.validate_source(root, "0" * 40)
            (root / "install-stamp.json").write_text(json.dumps({"commit": commit, "displayVersion": "0.23.1", "runtimeDir": "tools", "updateMechanism": "external"}))
            marker = Path(tmp) / "etc/hermes/image-provenance.json"
            module.write_provenance(root, marker)
            provenance = json.loads(marker.read_text())
            self.assertEqual(provenance["revision"], commit)
            self.assertEqual(provenance["version"], "0.23.1")
            self.assertEqual(marker.stat().st_mode & 0o777, 0o444)
            self.assertEqual((root / ".install_method").read_text(), "docker\n")
            self.assertEqual(json.loads((root / "install-stamp.json").read_text())["pmRuntime"], str(root / "pm-runtime"))
    def test_cli_executes_pipeline_with_fail_fast_checks(self):
        import importlib.util
        import os
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("release_builder", ROOT / "scripts/build_hermes.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "main"), "Docker must execute the build, not just define a plan")
        commit = "3b421578588243f920886e6fe79a6faa87ea026e"
        with patch.object(module, "validate_source") as validate, \
             patch.object(module, "provision_tools", return_value=(Path("/managed/python"), Path("/managed/uv"))) as provision, \
             patch.object(module.subprocess, "run") as run, \
             patch.object(module, "write_provenance") as provenance, \
             patch.object(module.shutil, "rmtree"), \
             patch.dict(os.environ):
            module.main(["--root", "/opt/hermes-agent", "--requirements", "/app/requirements.txt",
                         "--commit", commit, "--provenance", "/etc/hermes/image-provenance.json"])
        validate.assert_called_once_with(Path("/opt/hermes-agent"), commit)
        provision.assert_called_once_with(Path("/opt/hermes-agent"))
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))
        self.assertTrue(any("sqlite3" in " ".join(call.args[0]) for call in run.call_args_list))
        provenance.assert_called_once()

    def test_provisions_toolchain_only_from_pm_store(self):
        import importlib.util
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        spec = importlib.util.spec_from_file_location("release_builder", ROOT / "scripts/build_hermes.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "provision_tools"), "stage PM-owned tools inside image, not volume")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pm").mkdir()
            (root / "pm/lock.json").write_text(json.dumps({"packages": {"node": {"version": "26.12.1"}}}))
            def package(name):
                if name == "uv":
                    raise ValueError("uv is internal PM tooling, not an application package")
                binary = root / "tools" / name / "bin" / name
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.touch()
                binary.chmod(0o755)
                if name == "npm":
                    npx = binary.with_name("npx")
                    npx.touch()
                    npx.chmod(0o755)
                return SimpleNamespace(binary=binary)
            uv_binary = root / "tools/uv/bin/uv"
            uv_binary.parent.mkdir(parents=True)
            uv_binary.touch()
            fake_pm = SimpleNamespace(ensure=Mock(), installed_package=package, uv_launcher=lambda name: uv_binary,
                                      env_for=lambda *a, **k: {"PATH": str(root / "tools/npm/bin")})
            with patch.dict("sys.modules", {"pm": fake_pm}):
                python, uv = module.provision_tools(root, bindir=root / "bin")
            self.assertEqual([c.args[0] for c in fake_pm.ensure.call_args_list], ["python", "uv", "node", "pnpm", "npm"])
            self.assertTrue(python.is_relative_to(root / "tools"))
            self.assertTrue(uv.is_relative_to(root / "tools"))
            self.assertTrue((root / "bin/node").is_symlink())
            self.assertTrue((root / "bin/pnpm").is_symlink())


if __name__ == "__main__":
    unittest.main()
