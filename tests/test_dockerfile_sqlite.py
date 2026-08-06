from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
INTEGRATION_TEST = ROOT / "tests" / "test_sqlite_image.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "sqlite-image.yml"


class DockerfileSQLiteTests(unittest.TestCase):
    def test_builds_and_loads_fixed_sqlite(self):
        self.assertIn("AS sqlite-builder", DOCKERFILE)
        self.assertIn("SQLITE_AUTOCONF_VERSION=3510300", DOCKERFILE)
        self.assertIn(
            "SQLITE_SHA256=81f5be397049b0cae1b167f2225af7646fc0f82e4a9b3c48c9ea3a533e21d77a",
            DOCKERFILE,
        )
        self.assertIn("sha256sum -c -", DOCKERFILE)
        self.assertIn("--fts5", DOCKERFILE)
        self.assertIn("COPY --from=sqlite-builder /opt/sqlite/ /usr/local/", DOCKERFILE)
        self.assertIn("ldconfig", DOCKERFILE)
        self.assertIn("assert sqlite3.sqlite_version_info >= (3, 51, 3)", DOCKERFILE)
        self.assertIn("sqlite_compileoption_used('ENABLE_FTS5')", DOCKERFILE)

    def test_docker_integration_test_is_wired_into_ci(self):
        self.assertIn("AS sqlite-runtime", DOCKERFILE)
        script = INTEGRATION_TEST.read_text(encoding="utf-8")
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("docker build --target sqlite-runtime", script)
        self.assertIn("docker run --rm", script)
        self.assertIn("/usr/local/lib/libsqlite3.so.0", script)
        self.assertIn("tests/test_sqlite_image.sh", workflow)
        self.assertIn("actions/checkout@11d5960a326750d5838078e36cf38b85af677262", workflow)
        self.assertNotIn("actions/checkout@v4", workflow)


if __name__ == "__main__":
    unittest.main()
