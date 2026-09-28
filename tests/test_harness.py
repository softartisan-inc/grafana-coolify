import shutil
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

from support import ROOT, require_harness, run

sys.path.insert(0, str(ROOT / "harness"))
import stack  # noqa: E402

STACK = [sys.executable, str(ROOT / "harness" / "stack.py")]


class InterpolationTest(unittest.TestCase):
    def test_compose_rules(self):
        env = {"SET": "v", "EMPTY": ""}
        cases = {
            "${SET}": "v",
            "${UNSET:-d}": "d",
            "${EMPTY:-d}": "d",
            "${EMPTY-d}": "",
            "$$1 ${SET}": "$1 v",
            "--flag=${EMPTY:-}": "--flag=",
            "$SET": "v",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(stack.interpolate(value, env), expected)

    def test_required_variable(self):
        with self.assertRaisesRegex(stack.HarnessError, "IP_HASH_SALT"):
            stack.interpolate("${IP_HASH_SALT:?IP_HASH_SALT is required}", {"IP_HASH_SALT": ""})


class RewriteTest(unittest.TestCase):
    def test_whole_path_segments_only(self):
        rewrite = stack.rewriter([("/loki", "/data/loki"), ("/guard", "/repo/config"), ("/etc/loki/loki.yaml", "/repo/config/loki/loki.yaml")], "127.0.10.2")
        self.assertEqual(rewrite("-config.file=/etc/loki/loki.yaml"), "-config.file=/repo/config/loki/loki.yaml")
        self.assertEqual(rewrite("/loki"), "/data/loki")
        self.assertEqual(rewrite("/loki/chunks"), "/data/loki/chunks")
        self.assertEqual(rewrite("http://loki:3100"), "http://loki:3100")
        self.assertEqual(rewrite("/lokix"), "/lokix")
        self.assertEqual(rewrite("/guard/a=1;/guard/b=2"), "/repo/config/a=1;/repo/config/b=2")
        self.assertEqual(rewrite("--web.listen-address=0.0.0.0:9090"), "--web.listen-address=127.0.10.2:9090")

    def test_image_repository(self):
        self.assertEqual(stack.image_repository("grafana/loki:3.7.8"), "grafana/loki")
        self.assertEqual(stack.image_repository("quay.io/prometheus/node-exporter:v1.12.1"), "quay.io/prometheus/node-exporter")

    def test_hosts_block_round_trip(self):
        original = "127.0.0.1 localhost\n"
        with_block = original + stack.hosts_block()
        self.assertIn("127.0.10.2 loki", with_block)
        self.assertEqual(stack.without_block(with_block), original)


class ContentFilesTest(unittest.TestCase):
    """.harness/coolify/ holds what Coolify writes: each content file under its hashed name."""

    def hashed_loki(self):
        return next(hashed for hashed, source in stack.content_sources().items() if source == "./config/loki/loki.yaml")

    def test_grpc_ports_come_from_the_configs(self):
        self.assertEqual(stack.extra_ports("loki"), [("127.0.0.1", 9095)])
        self.assertEqual(stack.extra_ports("tempo"), [("127.0.0.1", 9096)])
        self.assertEqual(stack.extra_ports("prometheus"), [])

    def test_content_file_gets_its_hashed_name(self):
        written = stack.materialize(self.hashed_loki(), ROOT / "config")
        self.assertRegex(written.name, r"^loki\.[0-9a-f]{8}\.yaml$")
        self.assertEqual(written.read_bytes(), (ROOT / "config" / "loki" / "loki.yaml").read_bytes())

    def test_directory_and_missing_file_are_reproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            shutil.copytree(ROOT / "config", config, ignore=shutil.ignore_patterns("__pycache__"))
            (config / "loki" / "loki.yaml").unlink()
            self.assertFalse(stack.materialize(self.hashed_loki(), config).exists())
            (config / "loki" / "loki.yaml").mkdir()
            self.assertTrue(stack.materialize(self.hashed_loki(), config).is_dir())
        self.assertTrue(stack.materialize(self.hashed_loki(), ROOT / "config").is_file())


class StorageTrioTest(unittest.TestCase):
    """The storage trio starts from compose.template.yaml and answers readiness by service name."""

    def setUp(self):
        require_harness(self)
        run([*STACK, "down"], timeout=120)

    def tearDown(self):
        run([*STACK, "down"], timeout=120)

    def test_trio_starts_and_stops(self):
        result = run([*STACK, "up", "--only", "loki,tempo,prometheus"], timeout=400)
        self.assertEqual(result.returncode, 0, result.stdout)
        for url in ("http://loki:3100/ready", "http://tempo:3200/ready", "http://prometheus:9090/-/ready"):
            with self.subTest(url=url), urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        self.assertEqual(run([*STACK, "down"], timeout=120).returncode, 0)
        self.assertNotIn(stack.HOSTS_BEGIN, stack.HOSTS.read_text(encoding="utf-8"))
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(("127.0.10.2", 3100)), 0)

    def test_busy_address_is_reported(self):
        with socket.socket() as blocker:
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            blocker.bind(("127.0.10.2", 3100))
            blocker.listen(1)
            result = run([*STACK, "up", "--only", "loki"], timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("127.0.10.2:3100", result.stdout)
        self.assertIn("already in use", result.stdout)


if __name__ == "__main__":
    unittest.main()
