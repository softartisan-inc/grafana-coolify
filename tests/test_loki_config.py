import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run, validator_env

CONFIG = ROOT / "config" / "loki" / "loki.yaml"


class LokiConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, path):
        cmd = [binary("loki"), f"-config.file={path}", "-config.expand-env=true", "-verify-config"]
        return run(cmd, env=validator_env(self.tmp.name))

    def test_official_validator_accepts_config(self):
        result = self.verify(CONFIG)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("config is valid", result.stdout)

    def test_official_validator_rejects_broken_config(self):
        broken = Path(self.tmp.name) / "broken.yaml"
        broken.write_text(CONFIG.read_text(encoding="utf-8").replace("retention_enabled", "retention_enabledd"), encoding="utf-8")
        self.assertNotEqual(self.verify(broken).returncode, 0)

    def test_otlp_labels_and_metadata(self):
        otlp = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["limits_config"]["otlp_config"]["resource_attributes"]
        self.assertIs(otlp["ignore_defaults"], True)
        by_action = {rule["action"]: rule["attributes"] for rule in otlp["attributes_config"]}
        self.assertEqual(by_action["index_label"], ["project", "env", "service.name"])
        self.assertEqual(by_action["structured_metadata"], ["tenant"])

    def test_schema_and_retention(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        schema = doc["schema_config"]["configs"][0]
        self.assertEqual((schema["store"], schema["schema"], schema["index"]["period"]), ("tsdb", "v13", "24h"))
        self.assertIs(doc["compactor"]["retention_enabled"], True)
        self.assertEqual(doc["compactor"]["delete_request_store"], "filesystem")
        limits = doc["limits_config"]
        self.assertEqual(limits["retention_period"], "${LOKI_RETENTION_DEFAULT}")
        self.assertEqual(limits["retention_stream"], [{"selector": '{env="prod"}', "priority": 1, "period": "${LOKI_RETENTION_PROD}"}])
        self.assertIs(limits["allow_structured_metadata"], True)

    def test_binds_to_bind_addr_and_ring_is_local(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(doc["server"]["http_listen_address"], "${BIND_ADDR}")
        self.assertEqual(doc["common"]["ring"]["kvstore"]["store"], "inmemory")


if __name__ == "__main__":
    unittest.main()
