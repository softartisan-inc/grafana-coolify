import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

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

    def test_hosts_block_maps_the_network_aliases(self):
        """The bench resolves the gc-* aliases of the `coolify` network like the services' names."""
        block = stack.hosts_block()
        for line in ("127.0.10.2 loki gc-loki", "127.0.10.3 tempo gc-tempo", "127.0.10.4 prometheus gc-prometheus", "127.0.10.5 alloy gc-alloy"):
            with self.subTest(line=line):
                self.assertIn(line + "\n", block)
        self.assertIn("127.0.10.6 alloy-gateway\n", block)

    def test_network_aliases_of_every_network(self):
        services = {
            "a": {"networks": {"default": {}, "coolify": {"aliases": ["gc-a"]}, "other": {"aliases": ["x-a"]}}},
            "b": {"networks": ["default"]},
            "c": {},
            "d": {"networks": {"coolify": None}},
        }
        self.assertEqual(stack.network_aliases(services), {"a": ["gc-a", "x-a"]})

    def test_dangling_begin_is_stripped(self):
        """A write cut short leaves BEGIN without END: its partial lines go, other lines stay."""
        original = "127.0.0.1 localhost\n"
        truncated = stack.hosts_block()[:60]
        self.assertNotIn(stack.HOSTS_END, truncated)
        self.assertEqual(stack.without_block(original + truncated), original)
        self.assertEqual(stack.without_block(original + truncated.rstrip("\n") + "\n10.0.0.1 other\n"), original + "10.0.0.1 other\n")
        self.assertEqual(stack.without_block(original + stack.HOSTS_BEGIN + "\n"), original)

    def test_hosts_written_from_a_complete_temp_file(self):
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append((cmd, Path(cmd[-2]).read_text(encoding="utf-8")))

        with mock.patch.object(stack.subprocess, "run", side_effect=fake_run):
            stack.write_hosts("127.0.0.1 localhost\n")
        [(cmd, content)] = seen
        self.assertEqual(cmd[:3], ["sudo", "-n", "cp"])
        self.assertEqual(cmd[-1], str(stack.HOSTS))
        self.assertEqual(content, "127.0.0.1 localhost\n")
        self.assertFalse(Path(cmd[-2]).exists(), "temp file left behind")


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
        # What Coolify writes: the compose content:, stripped of full-line comments by render.py.
        stripped = stack.render.strip_comments((ROOT / "config" / "loki" / "loki.yaml").read_text(encoding="utf-8"), "loki.yaml")
        self.assertEqual(written.read_bytes(), stripped.encode("utf-8"))
        self.assertIn(hashlib.sha256(written.read_bytes()).hexdigest()[:8], written.name)

    def test_directory_and_missing_file_are_reproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            shutil.copytree(ROOT / "config", config, ignore=shutil.ignore_patterns("__pycache__"))
            (config / "loki" / "loki.yaml").unlink()
            self.assertFalse(stack.materialize(self.hashed_loki(), config).exists())
            (config / "loki" / "loki.yaml").mkdir()
            self.assertTrue(stack.materialize(self.hashed_loki(), config).is_dir())
        self.assertTrue(stack.materialize(self.hashed_loki(), ROOT / "config").is_file())


class ProcessIdentityTest(unittest.TestCase):
    """A state.json entry is ours only while its PID still has the start time recorded at spawn."""

    def setUp(self):
        self.pid = os.getpid()
        self.signals = []
        patcher = mock.patch.object(stack.os, "killpg", side_effect=lambda pid, sig: self.signals.append((pid, sig)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_matching_entry_is_recognised(self):
        entry = {"pid": self.pid, "start": stack.start_time(self.pid)}
        self.assertIsNotNone(entry["start"])
        self.assertTrue(stack.alive(entry))

    def test_reused_pid_is_not_ours(self):
        entry = {"pid": self.pid, "start": str(int(stack.start_time(self.pid)) + 1)}
        self.assertFalse(stack.alive(entry))
        stack.terminate(entry)
        self.assertEqual(self.signals, [])

    def test_entry_without_identity_is_not_ours(self):
        entry = {"pid": self.pid, "ip": "127.0.10.2"}
        self.assertFalse(stack.alive(entry))
        stack.terminate(entry)
        self.assertEqual(self.signals, [])

    def test_stale_state_does_not_block_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"processes": {"loki": {"pid": self.pid, "start": "1", "ip": "127.0.10.2"}}}), encoding="utf-8")
            with mock.patch.object(stack, "STATE", state):
                self.assertFalse(any(stack.alive(p) for p in stack.load_state()["processes"].values()))


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
