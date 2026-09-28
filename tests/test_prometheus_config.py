import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run

CONFIG = ROOT / "config" / "prometheus" / "prometheus.yml"
EXPECTED_TARGETS = {
    "prometheus": "prometheus:9090",
    "loki": "loki:3100",
    "tempo": "tempo:3200",
    "alloy": "alloy:12345",
    "alloy-gateway": "alloy-gateway:12345",
    "node-exporter": "node-exporter:9100",
}


class PrometheusConfigTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def test_promtool_accepts_config(self):
        result = run([binary("promtool"), "check", "config", CONFIG])
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_promtool_rejects_broken_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "prometheus.yml"
            broken.write_text(CONFIG.read_text(encoding="utf-8").replace("promote_resource_attributes", "promote_attributes"), encoding="utf-8")
            self.assertNotEqual(run([binary("promtool"), "check", "config", broken]).returncode, 0)

    def test_otlp_promotion(self):
        self.assertEqual(self.doc["otlp"]["promote_resource_attributes"], ["project", "env", "tenant"])
        self.assertNotIn("service.name", self.doc["otlp"]["promote_resource_attributes"])

    def test_out_of_order_window(self):
        self.assertEqual(self.doc["storage"]["tsdb"]["out_of_order_time_window"], "30m")

    def test_scrape_targets(self):
        jobs = {job["job_name"]: job["static_configs"][0]["targets"] for job in self.doc["scrape_configs"]}
        self.assertEqual(jobs, {name: [target] for name, target in EXPECTED_TARGETS.items()})


if __name__ == "__main__":
    unittest.main()
