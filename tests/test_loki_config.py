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

    def test_global_budgets_are_explicit(self):
        """Stream cap and ingestion rate are stated, not Loki defaults (5000 streams, 4/6 MB)."""
        limits = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["limits_config"]
        self.assertEqual(limits["max_global_streams_per_user"], 10000)
        self.assertEqual(limits["ingestion_rate_mb"], 16)
        self.assertEqual(limits["ingestion_burst_size_mb"], 32)

    def test_per_stream_rate_is_explicit_and_fits_the_global_budget(self):
        """Per-stream rate is stated, not Loki's 3 MB/s (burst 15 MB) that would 429 a single-stream backlog."""
        limits = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["limits_config"]
        self.assertEqual(limits.get("per_stream_rate_limit"), "8MB")
        self.assertEqual(limits.get("per_stream_rate_limit_burst"), "24MB")
        rate, burst = (int(limits[key].removesuffix("MB")) for key in ("per_stream_rate_limit", "per_stream_rate_limit_burst"))
        # Half the tenant rate: one stream (a replayed backlog, a Faro stream) cannot starve the others.
        self.assertEqual(rate * 2, limits["ingestion_rate_mb"])
        # Records of 1 to 4 KiB: the burst holds 10 concurrent 2 MiB batches and 3 of the largest (8 MiB),
        # within the tenant burst.
        self.assertGreaterEqual(burst, max(10 * 2, 3 * 8))
        self.assertLessEqual(burst, limits["ingestion_burst_size_mb"])

    def test_binds_to_bind_addr_and_ring_is_local(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(doc["server"]["http_listen_address"], "${BIND_ADDR}")
        self.assertEqual(doc["common"]["ring"]["kvstore"]["store"], "inmemory")


if __name__ == "__main__":
    unittest.main()
