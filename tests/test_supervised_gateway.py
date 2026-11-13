"""The wrapper owns its subprocesses; tests never spawn a live gateway."""
import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
with TemporaryDirectory() as home, patch.dict(os.environ, {
    "HERMES_HOME": home, "ADMIN_USERNAME": "unit-test", "ADMIN_PASSWORD": "unit-test-only",
}):
    spec = importlib.util.spec_from_file_location("supervised_server", ROOT / "server.py")
    assert spec is not None and spec.loader is not None
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)


class SupervisedGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_restart_exit_is_handed_back_to_supervisor(self):
        async def no_output():
            if False:
                yield b""
        process = MagicMock()
        process.stdout = no_output()
        process.returncode = 75
        process.pid = 12345
        gateway = server.Gateway()
        gateway.proc = process
        gateway.state = "running"
        with patch.object(gateway, "_supervise_respawn", AsyncMock()) as respawn:
            await gateway._drain(process)
            await asyncio.sleep(0)
            respawn.assert_awaited_once_with(12345)

    async def test_gateway_declares_external_supervisor(self):
        process = MagicMock(returncode=None)
        process.returncode = None
        spawn = AsyncMock(return_value=process)
        gateway = server.Gateway()
        with patch.object(server, "build_hermes_env", return_value={"LLM_MODEL": "unit-test-model"}), \
             patch.object(server, "write_config_yaml"), \
             patch.object(server, "read_env", return_value={}), \
             patch.object(server.asyncio, "create_subprocess_exec", spawn), \
             patch.object(gateway, "_drain", AsyncMock()):
            await gateway.start()
            await asyncio.sleep(0)
        self.assertEqual(gateway.state, "running")
        self.assertIn("--external-supervisor", spawn.call_args.args)
        self.assertIn("--replace", spawn.call_args.args)


class ConfigSafetyTests(unittest.TestCase):
    def test_persists_through_native_atomic_config_writer(self):
        writer = MagicMock()
        native = SimpleNamespace(atomic_config_replace=writer)
        with TemporaryDirectory() as home, patch.object(server, "HERMES_HOME", home), \
             patch.dict(sys.modules, {"hermes_cli.config": native}):
            config = Path(home, "config.yaml")
            config.write_text("# Keep owner comments\nmodel:\n  provider: openai-codex\nmcp_servers:\n  owned: {command: example}\n")
            server.write_config_yaml({"LLM_MODEL": "unit-test-model"})
            writer.assert_called_once()
            self.assertEqual(writer.call_args.args[0], config)
            saved = writer.call_args.args[1]
            self.assertEqual(saved["model"]["provider"], "openai-codex")
            self.assertEqual(saved["mcp_servers"], {"owned": {"command": "example"}})

    def test_bad_config_is_not_replaced_with_defaults(self):
        with TemporaryDirectory() as home, patch.object(server, "HERMES_HOME", home):
            config = Path(home, "config.yaml")
            content = "model: [invalid\n"
            config.write_text(content)
            with self.assertRaises(ValueError):
                server.write_config_yaml({"LLM_MODEL": "unit-test-model"})
            self.assertEqual(config.read_text(), content)


class StartupOwnershipTests(unittest.TestCase):
    def test_startup_does_not_overwrite_volume_install_ownership(self):
        start = (ROOT / "start.sh").read_text()
        self.assertNotIn("> /data/.hermes/.install_method", start)


class ImageIdentityTests(unittest.TestCase):
    def test_built_version_wins_over_runtime_environment(self):
        self.assertTrue(callable(getattr(server, "installed_hermes_version", None)))
        with TemporaryDirectory() as home:
            stamp = Path(home, "install-stamp.json")
            stamp.write_text('{"displayVersion":"0.23.1","updateMechanism":"external"}')
            with patch.dict(os.environ, {"HERMES_REF": "v0.99.0"}):
                self.assertEqual(server.installed_hermes_version(stamp), "v0.23.1")


if __name__ == "__main__":
    unittest.main()
