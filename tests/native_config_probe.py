"""Run inside the built image: exercise the actual v0.23.1 config writer."""
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


def main():
    with TemporaryDirectory(prefix="hermes-config-probe-") as home:
        os.environ.update({
            "HERMES_HOME": home,
            "ADMIN_USERNAME": "ci-fixture",
            "ADMIN_PASSWORD": "ci-fixture-only",
            "HERMES_REF": "v0.99.0",  # Must not override built image identity.
        })
        config = Path(home, "config.yaml")
        config.write_text(
            "# CI comment preservation sentinel\n"
            "model:\n  provider: openai-codex\n  default: old-fixture-model\n"
            "mcp_servers:\n  fixture: {command: fixture-never-executed}\n"
            "gateway:\n  multiplex_profiles: true\n"
            "  profile_routes:\n    - {profile: isolated-fixture, platform: telegram, chat_id: fixture}\n"
        )
        config.chmod(0o600)
        spec = importlib.util.spec_from_file_location("image_config_server", "/app/server.py")
        assert spec is not None and spec.loader is not None
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        assert server.HERMES_VERSION == "v0.23.1", server.HERMES_VERSION
        before = yaml.safe_load(config.read_text())
        server.write_config_yaml({"LLM_MODEL": "new-fixture-model"})
        content = config.read_text()
        after = yaml.safe_load(content)
        assert "# CI comment preservation sentinel" in content
        assert after["mcp_servers"] == before["mcp_servers"]
        assert after["gateway"] == before["gateway"]
        assert after["model"]["provider"] == "openai-codex"
        assert after["model"]["default"] == "new-fixture-model"
        assert config.stat().st_mode & 0o077 == 0
        invalid = "model: [invalid\n"
        config.write_text(invalid)
        try:
            server.write_config_yaml({"LLM_MODEL": "new-fixture-model"})
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed config was not rejected")
        assert config.read_text() == invalid
    print("Native config preservation and image identity probe passed")


if __name__ == "__main__":
    main()
