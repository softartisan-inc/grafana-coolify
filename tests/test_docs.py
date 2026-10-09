import re
import tempfile
import unittest
from pathlib import Path

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
        variables = (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "ALERT_EMAILS",
            "ALERT_ERROR_RATE",
            "ALERT_P95_MS",
            "ALERT_P95_MIN_CALLS",
            "ALERT_SILENCE_MIN",
            "ALERT_DISK_PCT",
        )
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

    def test_coolify_network_aliases_are_documented(self):
        """Spike S1: gc-* aliases, Grafana on the coolify network, internal GRAFANA_URL."""
        spikes = (ROOT / "docs" / "spikes.md").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for text in (
            "LOKI_INTERNAL_URL=http://gc-loki:3100",
            "TEMPO_INTERNAL_URL=http://gc-tempo:3200",
            "PROMETHEUS_INTERNAL_URL=http://gc-prometheus:9090",
            "ALLOY_INTERNAL_URL=http://gc-alloy:4318",
            "http://grafana-<uuid>:3000",
            "docker ps --format '{{.Names}}' | grep -E '^grafana-[a-z0-9]+$'",
            "Ne jamais désactiver l'option tant que le compose déployé ne déclare pas lui-même le réseau",
            "Grafana not healthy after 120s",
            "Repli : noms nus",
            "Consistent Container Names",
            "networks.coolify.name",
            "wget -qO- http://gc-loki:3100/ready",
            "wget -qO- http://loki:3100/ready",
            "Retour arrière",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)
        self.assertNotIn("http://loki-<uuid>:3100", README)
        for doc in (README, spikes):
            self.assertNotIn("grep -i grafana", doc)
        for text in ("loki-<uuid>-<horodatage>", "applicationParser", "gc-loki", "l. 1590-1603",
                     "Consistent Container Names"):
            with self.subTest(text=text):
                self.assertIn(text, spikes)
        self.assertIn("http://gc-loki:3100", env_example)

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


def heading_anchors(path):
    """GitHub anchors of a Markdown file: lower case, punctuation dropped, spaces as hyphens."""
    anchors, seen, fence = set(), {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fence = not fence
            continue
        match = None if fence else re.match(r"#{1,6} (.+)", line)
        if match:
            slug = re.sub(r"[^\w\- ]", "", match.group(1).strip().lower()).replace(" ", "-")
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def broken_links(markdown):
    """Relative links of a Markdown file whose target file or heading anchor does not exist."""
    text = re.sub(r"```.*?```", "", markdown.read_text(encoding="utf-8"), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        path, _, anchor = target.partition("#")
        destination = (markdown.parent / path) if path else markdown
        if not destination.exists():
            broken.append(f"{target} (missing file)")
        elif anchor and destination.suffix == ".md" and anchor not in heading_anchors(destination):
            broken.append(f"{target} (missing anchor)")
    return broken


class DocsLinksTest(unittest.TestCase):
    """Every relative link of docs/ points to an existing file and, if any, an existing heading."""

    def test_relative_links_and_anchors_resolve(self):
        pages = sorted((ROOT / "docs").glob("**/*.md"))
        self.assertGreater(len(pages), 10)
        for page in pages:
            with self.subTest(page=str(page.relative_to(ROOT))):
                self.assertEqual(broken_links(page), [])

    def test_checker_reports_a_missing_file_and_a_missing_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.md"
            page.write_text("# Titre `code`\n\n[ok](#titre-code) [a](#absent) [f](absent.md) [w](https://x.test)\n", encoding="utf-8")
            self.assertEqual(broken_links(page), ["#absent (missing anchor)", "absent.md (missing file)"])


if __name__ == "__main__":
    unittest.main()
