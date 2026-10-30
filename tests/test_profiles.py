"""Continuous profiling: Pyroscope configuration, the Alloy profiles path and their compose wiring.

The end-to-end admission checks (accepted and rejected profiles, labels stored by Pyroscope) are
the `profiles` section of scripts/smoke.py, run against the native bench."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from support import BIN, ROOT, binary, run, validator_env

sys.path.insert(0, str(ROOT / "scripts"))
import check  # noqa: E402

PYROSCOPE = ROOT / "config" / "pyroscope" / "pyroscope.yaml"
ALLOY = ROOT / "config" / "alloy" / "config.alloy"


def profiles_path():
    text = ALLOY.read_text(encoding="utf-8")
    return text[text.index('pyroscope.receive_http "default"') :]


class PyroscopeConfigTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(PYROSCOPE.read_text(encoding="utf-8"))

    def test_v2_single_node_on_the_data_volume(self):
        self.assertEqual(self.doc["architecture_storage"], "v2")
        self.assertEqual(self.doc["storage"]["backend"], "filesystem")
        dirs = [
            self.doc["storage"]["filesystem"]["dir"],
            self.doc["metastore"]["data_dir"],
            self.doc["metastore"]["raft"]["dir"],
            self.doc["metastore"]["raft"]["snapshots_dir"],
        ]
        for path in dirs:
            with self.subTest(path=path):
                self.assertTrue(path.startswith("${PYROSCOPE_DATA_DIR}/"))

    def test_only_http_on_bind_addr_everything_else_on_loopback(self):
        server = self.doc["server"]
        self.assertEqual((server["http_listen_address"], server["http_listen_port"]), ("${BIND_ADDR}", 4040))
        self.assertEqual(server["grpc_listen_address"], "127.0.0.1")
        grpc = f"127.0.0.1:{server['grpc_listen_port']}"
        self.assertEqual(self.doc["metastore"]["address"], grpc)
        self.assertEqual(self.doc["query_backend"]["address"], grpc)
        self.assertEqual(self.doc["memberlist"]["bind_addr"], ["127.0.0.1"])
        self.assertEqual(self.doc["distributor"]["ring"]["instance_addr"], "127.0.0.1")
        self.assertEqual(self.doc["segment_writer"]["lifecycler"]["address"], "127.0.0.1")
        self.assertEqual(self.doc["frontend"]["instance_addr"], "127.0.0.1")

    def test_grpc_port_differs_from_loki_and_tempo(self):
        ports = {self.doc["server"]["grpc_listen_port"]}
        for name in ("loki/loki.yaml", "tempo/tempo.yaml"):
            ports.add(yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))["server"]["grpc_listen_port"])
        self.assertEqual(len(ports), 3)

    def test_retention_self_profiling_and_usage_stats(self):
        self.assertEqual(self.doc["limits"]["retention_period"], "${PYROSCOPE_RETENTION:-168h}")
        # Self-profiles would carry no project label; usage stats would leave the host.
        self.assertIs(self.doc["self_profiling"]["disable_push"], True)
        self.assertIs(self.doc["analytics"]["reporting_enabled"], False)


class PyroscopeParseTest(unittest.TestCase):
    """Pyroscope rejects unknown fields: the parse-only run of check.py catches a misspelled key."""

    def setUp(self):
        if not (BIN / "pyroscope").exists():
            self.skipTest(f"{BIN / 'pyroscope'} missing: run tools/fetch-binaries.sh first")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def parse(self, path):
        cmd = [binary("pyroscope"), f"-config.file={path}", "-config.expand-env=true", f"-target={check.PYROSCOPE_PARSE_TARGET}"]
        result = run(cmd, env={**validator_env(self.tmp.name), "PYROSCOPE_DATA_DIR": self.tmp.name})
        return check.validator_passed(cmd, result.returncode, result.stdout), result.stdout

    def test_repository_config_parses(self):
        passed, output = self.parse(PYROSCOPE)
        self.assertTrue(passed, output)

    def test_misspelled_key_is_refused(self):
        broken = Path(self.tmp.name) / "broken.yaml"
        broken.write_text(PYROSCOPE.read_text(encoding="utf-8").replace("disable_push", "disable_pushh"), encoding="utf-8")
        passed, output = self.parse(broken)
        self.assertFalse(passed)
        self.assertIn("disable_pushh", output)

    def test_validator_passed(self):
        cmd = ["pyroscope", f"-target={check.PYROSCOPE_PARSE_TARGET}"]
        self.assertTrue(check.validator_passed(cmd, 1, "failed running pyroscope: " + check.PYROSCOPE_PARSED))
        self.assertFalse(check.validator_passed(cmd, 1, "failed parsing config: x.yaml: yaml: unmarshal errors"))
        self.assertFalse(check.validator_passed(cmd, 0, ""))
        self.assertTrue(check.validator_passed(["loki"], 0, ""))
        self.assertFalse(check.validator_passed(["loki"], 1, ""))


class AlloyProfilesPathTest(unittest.TestCase):
    def setUp(self):
        self.text = profiles_path()

    def test_receiver_listens_on_bind_addr_4040(self):
        self.assertRegex(self.text, r'listen_address = sys\.env\("BIND_ADDR"\)\s+listen_port\s+= 4040')

    def test_admission_rules(self):
        rules = re.findall(r"rule \{(.*?)\n  \}", self.text, re.S)
        keeps = {re.search(r'source_labels = \["(\w+)"\]', r).group(1): re.search(r"regex\s+= (.*)", r).group(1) for r in rules if '"keep"' in r}
        self.assertEqual(
            keeps["project"],
            'coalesce(string.replace(sys.env("PROJECTS"), ",", "|"), "[a-z0-9][a-z0-9-]{0,63}")',
        )
        self.assertEqual(keeps["env"], '"prod|preprod"')
        self.assertEqual(keeps["service_name"], '"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"')
        labelkeep = next(r for r in rules if '"labelkeep"' in r)
        self.assertIn('regex  = "__.+|service_name|project|env|role"', labelkeep)
        self.assertNotIn("tenant", labelkeep)

    def test_write_is_bounded(self):
        """receive_http waits for the write: the client sees at most a few seconds when Pyroscope is down."""
        self.assertIn('url                 = "http://pyroscope:4040"', self.text)
        self.assertIn('remote_timeout      = "5s"', self.text)
        self.assertIn("max_backoff_retries = 2", self.text)


class ComposeWiringTest(unittest.TestCase):
    def setUp(self):
        self.services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))["services"]

    def test_pyroscope_service(self):
        service = self.services["pyroscope"]
        self.assertNotIn("ports", service)
        self.assertEqual(service["expose"], ["4040"])
        self.assertEqual(service["networks"]["coolify"], {"aliases": ["gc-pyroscope"]})
        self.assertIn("pyroscope-data:/data", service["volumes"])
        self.assertEqual(service["environment"]["PYROSCOPE_DATA_DIR"], "/data/v2")
        self.assertEqual(service["environment"]["GOMEMLIMIT"], "900MiB")
        self.assertEqual(service["depends_on"], {"config-guard": {"condition": "service_completed_successfully"}})

    def test_alloy_exposes_profiles_after_the_routed_faro_port(self):
        expose = self.services["alloy"]["expose"]
        self.assertEqual(expose[0], "12347")
        self.assertIn("4040", expose)
        self.assertIn("PROJECTS", self.services["alloy"]["environment"])

    def test_grafana_setup_datasource_is_optional(self):
        self.assertEqual(self.services["grafana-setup"]["environment"]["PYROSCOPE_INTERNAL_URL"], "${PYROSCOPE_INTERNAL_URL:-}")


if __name__ == "__main__":
    unittest.main()
