import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run, validator_env

CONFIG = ROOT / "config" / "tempo" / "tempo.yaml"


class TempoConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, path):
        cmd = [binary("tempo"), f"-config.file={path}", "-config.expand-env=true", "-config.verify=true"]
        return run(cmd, env=validator_env(self.tmp.name))

    def test_official_validator_accepts_config(self):
        result = self.verify(CONFIG)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_official_validator_rejects_broken_config(self):
        broken = Path(self.tmp.name) / "broken.yaml"
        broken.write_text(CONFIG.read_text(encoding="utf-8").replace("block_retention", "block_retentionn"), encoding="utf-8")
        result = self.verify(broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("block_retentionn", result.stdout)

    def test_otlp_receiver_binds_to_bind_addr(self):
        protocols = self.doc["distributor"]["receivers"]["otlp"]["protocols"]
        self.assertEqual(protocols["grpc"]["endpoint"], "${BIND_ADDR}:4317")
        self.assertEqual(protocols["http"]["endpoint"], "${BIND_ADDR}:4318")
        self.assertEqual(self.doc["server"]["http_listen_address"], "${BIND_ADDR}")

    def test_retention(self):
        self.assertEqual(self.doc["compactor"]["compaction"]["block_retention"], "${TEMPO_RETENTION:-168h}")

    def test_metrics_generator(self):
        generator = self.doc["metrics_generator"]
        self.assertEqual(generator["processor"]["span_metrics"]["dimensions"], ["project", "env", "tenant", "http.route"])
        remote_write = generator["storage"]["remote_write"]
        self.assertEqual(remote_write, [{"url": "http://prometheus:9090/api/v1/write", "send_exemplars": "${ENABLE_EXEMPLARS:-false}"}])
        defaults = self.doc["overrides"]["defaults"]["metrics_generator"]
        self.assertEqual(defaults["processors"], ["span-metrics", "service-graphs"])
        self.assertEqual(defaults["max_active_series"], "${TEMPO_MAX_ACTIVE_SERIES:-100000}")


if __name__ == "__main__":
    unittest.main()
