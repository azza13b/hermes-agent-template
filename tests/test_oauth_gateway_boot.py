"""The Railway supervisor must auto-start a gateway authenticated with Codex OAuth."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_admin_server", ROOT / "server.py")
assert spec is not None and spec.loader is not None
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class OAuthGatewayBootTests(unittest.TestCase):
    def test_codex_refresh_token_allows_gateway_boot_without_api_key(self):
        with TemporaryDirectory() as home:
            Path(home, "auth.json").write_text(json.dumps({
                "providers": {"openai-codex": {"tokens": {
                    "access_token": "example-access", "refresh_token": "example-refresh"
                }}}
            }))
            with patch.object(server, "HERMES_HOME", home):
                self.assertTrue(server.is_config_complete({"LLM_MODEL": "gpt-6-sol"}))

    def test_missing_codex_refresh_token_does_not_allow_gateway_boot(self):
        with TemporaryDirectory() as home:
            Path(home, "auth.json").write_text(json.dumps({
                "providers": {"openai-codex": {"tokens": {"access_token": "expired"}}}
            }))
            with patch.object(server, "HERMES_HOME", home):
                self.assertFalse(server.is_config_complete({"LLM_MODEL": "gpt-6-sol"}))

    def test_missing_model_does_not_allow_gateway_boot(self):
        with TemporaryDirectory() as home:
            Path(home, "auth.json").write_text(json.dumps({
                "providers": {"openai-codex": {"tokens": {"refresh_token": "example"}}}
            }))
            with patch.object(server, "HERMES_HOME", home):
                self.assertFalse(server.is_config_complete({}))


if __name__ == "__main__":
    unittest.main()
