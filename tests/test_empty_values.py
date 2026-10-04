"""Empty operator variables (spike S5): Coolify passes a variable emptied in its UI as "" and skips
the compose `${VAR:-default}`. Every non-empty fallback of the template must be applied again by the
service that consumes the value, and with the same default as the template and .env.example.
The native bench replay (every fallback variable empty) is test_harness.EmptyValuesBenchTest."""

import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import yaml

from support import ROOT, binary, load_env_file, run, validator_env

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "config" / "grafana-setup"))
import alerting  # noqa: E402
import check  # noqa: E402

TEMPLATE = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
TEMPO = ROOT / "config" / "tempo" / "tempo.yaml"
LOKI = ROOT / "config" / "loki" / "loki.yaml"
ALLOY = ROOT / "config" / "alloy" / "config.alloy"
PROM_START = ROOT / "config" / "prometheus" / "start.sh"
FALLBACKS = check.fallback_defaults(TEMPLATE)
EMPTIED = dict.fromkeys(FALLBACKS, "")


def reapplied(name, default):
    """True when the consumer of `name` applies `default` itself to an empty value."""
    if name in ("TEMPO_RETENTION", "TEMPO_MAX_ACTIVE_SERIES", "ENABLE_EXEMPLARS"):
        return f"${{{name}:-{default}}}" in TEMPO.read_text(encoding="utf-8")
    if name.startswith("LOKI_RETENTION_"):
        return f"${{{name}:-{default}}}" in LOKI.read_text(encoding="utf-8")
    if name in ("PROM_RETENTION_TIME", "PROM_RETENTION_SIZE"):
        return f'"${{{name}:={default}}}"' in PROM_START.read_text(encoding="utf-8")
    if name.startswith("FARO_"):
        return f'coalesce(sys.env("{name}"), "{default}")' in ALLOY.read_text(encoding="utf-8")
    if name in alerting.DEFAULTS:
        return alerting.DEFAULTS[name] == default
    if name == "HOST_ENV":
        guard = (ROOT / "config" / "config-guard" / "guard.sh").read_text(encoding="utf-8")
        return alerting.host_env({name: ""}) == default and f"${{{name}:-{default}}}" in guard
    raise AssertionError(f"{name}: new ${{{name}:-{default}}} in compose.template.yaml, add its consumer to this test")


class FallbackInventoryTest(unittest.TestCase):
    def test_inventory_is_not_empty(self):
        self.assertIn("TEMPO_MAX_ACTIVE_SERIES", FALLBACKS)
        self.assertIn("PROM_RETENTION_TIME", FALLBACKS)

    def test_every_fallback_is_reapplied_by_its_consumer(self):
        for name, default in sorted(FALLBACKS.items()):
            with self.subTest(name=name):
                self.assertTrue(reapplied(name, default), f"{name}: an empty value would not take {default!r}")

    def test_defaults_match_env_example(self):
        example = load_env_file(ROOT / ".env.example")
        for name, default in sorted(FALLBACKS.items()):
            with self.subTest(name=name):
                self.assertEqual(example.get(name), default)

    def test_prometheus_command_carries_no_fallback(self):
        """The flags would reach Prometheus empty (exit 2: empty duration string): start.sh adds them."""
        prometheus = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))["services"]["prometheus"]
        self.assertFalse([arg for arg in prometheus["command"] if "${" in arg])
        self.assertEqual(prometheus["entrypoint"][0], "/bin/sh")
        self.assertRegex(prometheus["entrypoint"][1], r"^/etc/prometheus/start\.[0-9a-f]{8}\.sh$")


class TempoEmptyValuesTest(unittest.TestCase):
    """The real Tempo binary, every fallback variable empty: it must run with the defaults."""

    ADDR = "127.0.77.3"

    def setUp(self):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", 9096)) == 0:
                self.skipTest("127.0.0.1:9096 busy (a harness Tempo is running)")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_apply(self):
        config = Path(self.tmp.name) / "tempo.yaml"
        # No remote write target here: the generator would retry against prometheus:9090.
        config.write_text(TEMPO.read_text(encoding="utf-8").replace("http://prometheus:9090", f"http://{self.ADDR}:1"), encoding="utf-8")
        env = {**validator_env(self.tmp.name), **EMPTIED, "BIND_ADDR": self.ADDR}
        log = (Path(self.tmp.name) / "tempo.log").open("w")
        proc = subprocess.Popen([str(binary("tempo")), f"-config.file={config}", "-config.expand-env=true"], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            text = None
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://{self.ADDR}:3200/status/config", timeout=2) as resp:
                        text = resp.read().decode("utf-8")
                    break
                except OSError:
                    time.sleep(0.5)
            self.assertIsNotNone(text, (Path(self.tmp.name) / "tempo.log").read_text(encoding="utf-8")[-2000:])
        finally:
            proc.terminate()
            proc.wait(timeout=30)
            log.close()
        compactor = re.search(r"(?m)^compactor:\n(?:  .*\n)*?        block_retention: (\S+)", text)
        self.assertEqual(compactor.group(1), "168h0m0s")
        self.assertRegex(text, r"(?m)^ +max_active_series: 100000$")


class LokiEmptyValuesTest(unittest.TestCase):
    def test_defaults_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**validator_env(tmp), **EMPTIED}
            result = run([binary("loki"), f"-config.file={LOKI}", "-config.expand-env=true", "-verify-config", "-print-config-stderr"], env=env)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertRegex(result.stdout, r"(?m)^  retention_period: 1w$")
        self.assertRegex(result.stdout, r"(?m)^  - period: 30d$")


class PrometheusStartTest(unittest.TestCase):
    """config/prometheus/start.sh, under the machine sh and busybox sh (the image is busybox)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake = Path(self.tmp.name) / "prometheus"
        fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n', encoding="utf-8")
        fake.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, shell, path, **env):
        result = run([*shell, PROM_START, "--config.file=/etc/prometheus/prometheus.yml"], env={"PATH": f"{self.tmp.name}:{path}", **env})
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout.splitlines()

    def shells(self):
        return {"sh": (["sh"], "/usr/bin:/bin"), "busybox": ([str(binary("busybox")), "sh"], str(binary("busybox-applets")))}

    def test_empty_and_unset_take_the_defaults(self):
        expected = ["--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.retention.time=90d", "--storage.tsdb.retention.size=100GB"]
        for name, (shell, path) in self.shells().items():
            with self.subTest(shell=name, case="empty"):
                self.assertEqual(self.start(shell, path, PROM_RETENTION_TIME="", PROM_RETENTION_SIZE="", PROM_ENABLE_FEATURES=""), expected)
            with self.subTest(shell=name, case="unset"):
                self.assertEqual(self.start(shell, path), expected)

    def test_set_values_pass_through(self):
        for name, (shell, path) in self.shells().items():
            with self.subTest(shell=name):
                args = self.start(shell, path, PROM_RETENTION_TIME="30d", PROM_RETENTION_SIZE="20GB", PROM_ENABLE_FEATURES="exemplar-storage")
                self.assertEqual(args[1:], ["--storage.tsdb.retention.time=30d", "--storage.tsdb.retention.size=20GB", "--enable-feature=exemplar-storage"])


if __name__ == "__main__":
    unittest.main()
