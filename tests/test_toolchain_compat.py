"""Preserve CLI tools from the old Node image and sealed-code ownership."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "3b421578588243f920886e6fe79a6faa87ea026e"


def builder():
    spec = importlib.util.spec_from_file_location("toolchain_builder", ROOT / "scripts/build_hermes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeToolchainTests(unittest.TestCase):
    def test_npm_and_npx_survive_the_node_source_migration(self):
        module = builder()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pm").mkdir()
            (root / "pm/lock.json").write_text(json.dumps({"packages": {"node": {"version": "26.12.1"}}}))
            def package(name):
                binary = root / "tools" / name / "bin" / name
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.touch()
                binary.chmod(0o755)
                if name == "npm":
                    npx = binary.with_name("npx")
                    npx.touch()
                    npx.chmod(0o755)
                return SimpleNamespace(binary=binary)
            uv = package("uv").binary
            npm = package("npm").binary
            pm = SimpleNamespace(ensure=Mock(), installed_package=package,
                                 uv_launcher=lambda name: uv,
                                 env_for=lambda *a, **k: {"PATH": str(npm.parent)})
            with patch.dict(sys.modules, {"pm": pm}), patch.object(sys, "path", sys.path.copy()):
                module.provision_tools(root, bindir=root / "bin")
            self.assertIn("npm", [c.args[0] for c in pm.ensure.call_args_list])
            self.assertEqual((root / "bin/npm").resolve(), npm)
            self.assertEqual((root / "bin/npx").resolve(), npm.with_name("npx"))

    def test_finished_image_is_not_mistaken_for_a_mutable_checkout(self):
        module = builder()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            (root / ".git/HEAD").write_text("fixture")
            with patch.object(module, "validate_source"), \
                 patch.object(module, "provision_tools", return_value=(Path("/fixture/python"), Path("/fixture/uv"))), \
                 patch.object(module.subprocess, "run"), patch.object(module, "write_provenance"), \
                 patch.dict("os.environ"):
                module.main(["--root", str(root), "--requirements", "/fixture/requirements.txt",
                             "--commit", COMMIT, "--provenance", str(root / "image-provenance.json")])
            self.assertFalse((root / ".git").exists())


if __name__ == "__main__":
    unittest.main()
