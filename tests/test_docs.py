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
            "ingestion_rate_mb",
            "FARO_SERVICES",
            "unknown_service",
            "max_global_streams_per_user",
            "per_stream_rate_limit",
            "flatten",
            "tags.0",
            "scope.attributes",
            "Ne pas ajouter soi-même",
            "--only 1,2,7,8",
            "600 requêtes",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)

    def test_smoke_on_a_deployment_lists_its_variables(self):
        section = README[README.index("`scripts/smoke.py` interroge") :]
        section = section[: section.index("\n\n")]
        for name in ("IP_HASH_SALT", "FARO_API_KEY", "PROJECTS", "FARO_SERVICES"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", section)

    def test_spikes_tempo_zero_means_unlimited(self):
        spikes = (ROOT / "docs" / "spikes.md").read_text(encoding="utf-8")
        self.assertNotIn("plafond de séries à 0", spikes)
        self.assertIn("plafond illimité", spikes)

    def test_plan_b_operations_are_documented(self):
        for text in (
            "## Tableaux de bord et alertes",
            "@BotFather",
            "getUpdates",
            "GF_SMTP_HOST",
            "scripts/notify_test.py",
            "X-Disable-Provenance",
            "raw.githubusercontent.com",
            "GRAFANA_SETUP_MIRROR_URL",
            "GRAFANA_SETUP_TAG",
            "grafana-setup-content-v2",
            "git push origin <branche> grafana-setup-content-v2",
            "grafana-setup: done",
            "prérequis bloquant",
            "notifient personne",
            "legacy alerting provisioning API unavailable",
            "`service_name` pour Loki",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)
        variables = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALERT_EMAILS", "ALERT_ERROR_RATE", "ALERT_P95_MS", "ALERT_SILENCE_MIN", "ALERT_DISK_PCT")
        for name in (*variables, "CARDINALITY_ALERT_THRESHOLD", "HOST_ENV"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", README)

    def test_spikes_cover_plan_b(self):
        spikes = (ROOT / "docs" / "spikes.md").read_text(encoding="utf-8")
        self.assertIn("## S6 — `grafana-setup` du plan B sur la recette (bloquant avant la production)", spikes)
        self.assertIn("| S6 | | | | |", spikes)

    def test_first_coolify_deployment_findings_are_documented(self):
        for text in (
            "${VAR:?message}",  # Coolify sets VAR=message instead of refusing to deploy
            "1 an",  # Grafana service-account token expiry
            "rappel d'agenda",
            "vider le champ Domains",  # auto-generated alloy / alloy-gateway domains
            "https://faro.example.com:12347",
            "ne se renseignent pas ici",  # GF_SMTP_* belong to the Grafana service
            "[CMD] … base64 …",  # deployment logs hold the whole .env
            "faire tourner tous les secrets",
            "guillemets simples",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)

    def test_origin_regex_examples_are_single_quoted(self):
        """Inside double quotes, YAML rejects `\\.`: Traefik then drops the whole file."""
        example = (ROOT / "traefik" / "grafana-coolify.yaml.example").read_text(encoding="utf-8")
        for name, text in (("README.md", README), ("grafana-coolify.yaml.example", example)):
            with self.subTest(file=name):
                self.assertNotRegex(text, r'"\^https://[^"]*\\')
        self.assertIn("- '^https://([a-z0-9-]+\\.)?example\\.com$'", example)

    def test_mandatory_variables_are_explained(self):
        for name in ("IP_HASH_SALT", "FARO_API_KEY", "LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL", "GRAFANA_URL", "GRAFANA_SA_TOKEN"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", README)


if __name__ == "__main__":
    unittest.main()
