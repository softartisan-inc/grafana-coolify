import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run, validator_env

CONFIG = ROOT / "config" / "loki" / "loki.yaml"
MIB = 2**20


def parse_loki_bytes(value):
    """Size as Loki reads it (flagext.ByteSize, c2h5oh/datasize): "MB" is 2^20; "MiB" is rejected."""
    if value.endswith("MB") and value.removesuffix("MB").isdigit():
        return int(value.removesuffix("MB")) * MIB
    raise ValueError(f"unexpected size unit: {value!r}")


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
        rate = int(limits["per_stream_rate_limit"].removesuffix("MB"))
        # Half the tenant rate: one stream (a replayed backlog, a Faro stream) cannot starve the others.
        self.assertEqual(rate * 2, limits["ingestion_rate_mb"])
        # Loki's ByteSize is binary (c2h5oh/datasize): "24MB" is 24 MiB; *_mb are also x 2^20.
        burst = parse_loki_bytes(limits["per_stream_rate_limit_burst"])
        # Records of 1 to 4 KiB, 2048 per batch: batches of 2 to 8 MiB. The burst holds three of
        # the largest (8 MiB) and the 10 concurrent 2 MiB pushes.
        need = max(3 * 8 * MIB, 10 * 2 * MIB)
        self.assertGreaterEqual(burst, need)
        # The check bites: a burst below the stated need (20MB = 20 MiB) fails it.
        self.assertLess(parse_loki_bytes("20MB"), need)
        # Within the tenant burst.
        self.assertLessEqual(burst, limits["ingestion_burst_size_mb"] * MIB)

    def test_binds_to_bind_addr_and_ring_is_local(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(doc["server"]["http_listen_address"], "${BIND_ADDR}")
        self.assertEqual(doc["common"]["ring"]["kvstore"]["store"], "inmemory")


if __name__ == "__main__":
    unittest.main()
