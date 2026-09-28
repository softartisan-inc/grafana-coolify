import unittest

from support import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")


class ReadmeTest(unittest.TestCase):
    """The French README covers the deployment of spec 14 and the operator pitfalls."""

    def test_deployment_steps(self):
        for number in range(1, 9):
            self.assertIn(f"\n### {number}. ", README)

    def test_operator_pitfalls_are_documented(self):
        for text in (
            "Dynamic Configurations",
            "Connect To Predefined Network",
            "Is Literal?",
            "exited",
            "openssl passwd -apr1",
            "Service accounts",
            "sonde",
            "docs/spikes.md",
            "#9886",
            "openssl rand -hex 24",
            "Modifier une configuration",
            "alloy-data",
            "ipStrategy",
            "structural on bench",
            "GC_REVOKED_WAS_VALID",
            "GC_ALLOY_CONTAINER",
            "[card]",
            "Risque résiduel connu",
            "${VAR:-",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)

    def test_mandatory_variables_are_explained(self):
        for name in ("IP_HASH_SALT", "FARO_API_KEY", "LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL", "GRAFANA_URL", "GRAFANA_SA_TOKEN"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", README)


if __name__ == "__main__":
    unittest.main()
