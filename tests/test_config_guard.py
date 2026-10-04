import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from support import BIN, ROOT, binary, run, validator_env

GUARD = ROOT / "config" / "config-guard" / "guard.sh"
LOKI_CONFIG = ROOT / "config" / "loki" / "loki.yaml"
VALID_ENV = {
    "IP_HASH_SALT": "harnessNotASecret0000000000",
    "FARO_API_KEY": "harness-not-a-secret-0000000000",
    "HOST_MAP": "example.me=guest-front:prod",
    "RESERVED_SUBDOMAINS": "www,api",
    "TENANT_HOST_REGEX": r"^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$",
    "PROJECTS": "demo,other-project",
    "FARO_SERVICES": "web-app,desktop-app",
}
# Checked by grafana-setup only: a wrong value must never stop Loki, Tempo, Prometheus or Alloy.
SETUP_ONLY_VARIABLES = ("GRAFANA_URL", "GRAFANA_SA_TOKEN", "LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL")
# Loki retentions (Prometheus model.ParseDuration, then Loki's own 24h floor on retention_stream):
# LokiDurationCrossCheckTest holds both lists against the real binary.
LOKI_GOOD = (
    *("24h", "1d", "1w", "2w3d", "1y", "86400s", "1440m", "23h60m", "1d12h", "86400000ms", "1d0ms"),
    *("0y0w1d", "024h", "00000000000000000000024h", "292y", "292y24w", "9223372036s", "9223372036854ms"),
)
LOKI_BAD = (
    *("1h", "12h", "23h", "23h59m59s", "86399s", "1439m", "0d23h", "0y0w0d23h59m", "86399999ms"),
    # Repeated or out-of-order units: Loki answers "not a valid duration string".
    *("023h1h", "0h24h", "1h1d", "1d1d", "12h12h", "1ms1s", "24H"),
    # Above int64 nanoseconds: Loki answers "duration out of range".
    *("293y", "292y25w", "9223372037s", "9223372036855ms", "9999999999s", "99999999999s", "999999999y"),
    *("99999999999999999999y", "1e3d"),
)


class GuardCases:
    """The config-guard rules. Each subclass runs them under one shell."""

    def shell(self):
        """(argv prefix, PATH) of the shell under test."""
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "loki.0123abcd.yaml"
        self.file.write_text("auth_enabled: false\n", encoding="utf-8")
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, expected=None, **overrides):
        prefix, path = self.shell()
        env = {"PATH": path, **VALID_ENV}
        env["CONFIG_GUARD_EXPECTED"] = f"{self.file}={self.sha}" if expected is None else expected
        env.update(overrides)
        return run([*prefix, GUARD], env=env)

    def assert_fails(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(message, result.stdout)
        self.assertIn("no service will start", result.stdout)

    def test_valid_file_passes(self):
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"ok {self.file}", result.stdout)

    def test_several_entries(self):
        other = self.dir / "tempo.4567cdef.yaml"
        other.write_text("server: {}\n", encoding="utf-8")
        sha = hashlib.sha256(other.read_bytes()).hexdigest()
        result = self.guard(expected=f"{self.file}={self.sha};{other}={sha}")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"ok {other}", result.stdout)

    def test_missing_file(self):
        self.file.unlink()
        self.assert_fails(self.guard(), "missing")

    def test_empty_file(self):
        self.file.write_bytes(b"")
        self.assert_fails(self.guard(), "empty")

    def test_directory_instead_of_file(self):
        self.file.unlink()
        self.file.mkdir()
        self.assert_fails(self.guard(), "is a directory")

    def test_altered_content(self):
        self.file.write_text("auth_enabled: true\n", encoding="utf-8")
        self.assert_fails(self.guard(), "differs from source")

    def test_empty_expectations(self):
        self.assert_fails(self.guard(expected=""), "CONFIG_GUARD_EXPECTED is empty")

    def test_malformed_entry(self):
        self.assert_fails(self.guard(expected=f"{self.file}"), "malformed")

    def test_salt_rules(self):
        for salt in ["", "short", 'with"quote0123456789', "with space 0123456789", "with$dollar0123456789", "validSalt0123456789\nx"]:
            with self.subTest(salt=salt):
                self.assert_fails(self.guard(IP_HASH_SALT=salt), "IP_HASH_SALT")

    def test_faro_api_key_rules(self):
        """alloy always runs faro.receiver: an empty or short key must stop the deployment."""
        self.assertEqual(self.guard(FARO_API_KEY="0123456789abcdef0123456789abcdef0123456789abcdef").returncode, 0)
        for key in ["", "short-key-01234", "with space 0123456789", 'with"quote0123456789', "valid-faro-key-0123456789\nx"]:
            with self.subTest(key=key):
                self.assert_fails(self.guard(FARO_API_KEY=key), "FARO_API_KEY")

    def test_grafana_setup_variables_are_not_checked(self):
        """grafana-setup validates them and fails alone (exit 1): the guard must not block the stack."""
        for name in SETUP_ONLY_VARIABLES:
            for value in ("", f"{name} is required"):
                with self.subTest(name=name, value=value):
                    result = self.guard(**{name: value})
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertNotIn(name, result.stdout)

    def test_projects_rules(self):
        self.assertEqual(self.guard(PROJECTS="").returncode, 0)
        self.assertEqual(self.guard(PROJECTS="in-immo").returncode, 0)
        self.assertEqual(self.guard(PROJECTS="a" * 64 + ",b-1").returncode, 0)
        for value in ["demo, other", "Demo", "demo,", "a_b", "demo\nDemo Bad", "-demo", "demo,-other", "a" * 65]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(PROJECTS=value), "PROJECTS")

    def test_faro_services_rules(self):
        """Same rule as PROJECTS: the list is spliced into a regex and a Go template."""
        self.assertEqual(self.guard(FARO_SERVICES="").returncode, 0)
        self.assertEqual(self.guard(FARO_SERVICES="web").returncode, 0)
        for value in ["web, desktop", "Web", "web,", "-web", "a.b", "a|b", "a" * 65, "web\nx y"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(FARO_SERVICES=value), "FARO_SERVICES")

    def test_host_map_rules(self):
        self.assertEqual(self.guard(HOST_MAP="").returncode, 0)
        self.assertEqual(self.guard(HOST_MAP="a.me=web:prod,b.me=api:preprod").returncode, 0)
        for value in ["example.me", "Example.me=web:prod", "a.me=web", "a.me=web:prod,", "a.me=web:staging", "a.me=web:prod\nx"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(HOST_MAP=value), "HOST_MAP")

    def test_host_env_rules(self):
        for value in ("", "prod", "preprod"):
            with self.subTest(value=value):
                self.assertEqual(self.guard(HOST_ENV=value).returncode, 0)
        for value in ("staging", "Prod", "prod ", "prod\nx"):
            with self.subTest(value=value):
                self.assert_fails(self.guard(HOST_ENV=value), "HOST_ENV")

    def test_reserved_subdomains_rules(self):
        self.assertEqual(self.guard(RESERVED_SUBDOMAINS="").returncode, 0)
        for value in ["www,", "WWW", "www api", "www|api", "www\nx y"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(RESERVED_SUBDOMAINS=value), "RESERVED_SUBDOMAINS")

    def test_retention_and_limit_rules(self):
        """Empty takes the default downstream (Coolify passes "", spike S5); a set value must be valid and non-zero."""
        rules = {
            "TEMPO_RETENTION": (["168h", "1h30m", "720h"], ["0", "0h", "0h0m", "7d", "168", "168 h", "-1h", "168h\nx"]),
            "TEMPO_MAX_ACTIVE_SERIES": (["100000", "1"], ["0", "000", "-1", "1e5", "100 000", "unlimited", "10\nx"]),
            "LOKI_RETENTION_PROD": (["720h", "30d", "4w"], ["0", "0d", "30", "30 d", "thirty"]),
            "LOKI_RETENTION_DEFAULT": (["168h", "7d"], ["0s", "7", "7D"]),
            "PROM_RETENTION_TIME": (["90d", "1y", "2w3d"], ["0", "0d", "90", "90 d", "-90d"]),
            "PROM_RETENTION_SIZE": (["100GB", "512MiB"], ["0", "0GB", "100", "100 GB", "100gb", "-1GB"]),
            "ENABLE_EXEMPLARS": (["true", "false"], ["yes", "1", "True", "on", "false\nx"]),
        }
        for name in ("LOKI_RETENTION_PROD", "LOKI_RETENTION_DEFAULT"):
            good, bad = rules[name]
            good.extend(LOKI_GOOD)
            bad.extend(LOKI_BAD)
        for name, (good, bad) in rules.items():
            for value in ["", *good]:
                with self.subTest(name=name, value=value):
                    self.assertEqual(self.guard(**{name: value}).returncode, 0)
            for value in bad:
                with self.subTest(name=name, value=value):
                    self.assert_fails(self.guard(**{name: value}), name)

    def test_tenant_regex_needs_sub_group(self):
        self.assertEqual(self.guard(TENANT_HOST_REGEX="").returncode, 0)
        self.assert_fails(self.guard(TENANT_HOST_REGEX=r"^([a-z]+)\.example\.me$"), "TENANT_HOST_REGEX")
        multiline = VALID_ENV["TENANT_HOST_REGEX"] + "\nx"
        self.assert_fails(self.guard(TENANT_HOST_REGEX=multiline), "TENANT_HOST_REGEX")


class LokiDurationCrossCheckTest(unittest.TestCase):
    """LOKI_GOOD and LOKI_BAD as the real Loki reads them, so the guard cannot drift from Loki."""

    def setUp(self):
        if not (BIN / "loki").exists():
            self.skipTest(f"{BIN / 'loki'} missing: run tools/fetch-binaries.sh first")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def loki_accepts(self, name, value):
        env = {**validator_env(self.tmp.name), name: value}
        cmd = [binary("loki"), f"-config.file={LOKI_CONFIG}", "-config.expand-env=true", "-verify-config"]
        return run(cmd, env=env).returncode == 0

    def test_stream_retention_matches_loki(self):
        """retention_stream: Loki refuses what the guard refuses, 24h floor included."""
        for value in LOKI_GOOD:
            with self.subTest(value=value):
                self.assertTrue(self.loki_accepts("LOKI_RETENTION_PROD", value))
        for value in LOKI_BAD:
            with self.subTest(value=value):
                self.assertFalse(self.loki_accepts("LOKI_RETENTION_PROD", value))

    def test_default_retention_accepted_by_loki(self):
        """retention_period has no 24h floor in Loki: only the guard's accepted values are checked."""
        for value in LOKI_GOOD:
            with self.subTest(value=value):
                self.assertTrue(self.loki_accepts("LOKI_RETENTION_DEFAULT", value))
        self.assertTrue(self.loki_accepts("LOKI_RETENTION_DEFAULT", "23h"), "Loki now refuses 23h: update the docs")


class DashGuardTest(GuardCases, unittest.TestCase):
    """The POSIX sh of the machine (dash) with its GNU tools."""

    def shell(self):
        return ["sh"], os.environ["PATH"]


class BusyboxGuardTest(GuardCases, unittest.TestCase):
    """busybox sh with busybox applets only on PATH, as in the alpine image of config-guard."""

    def shell(self):
        return [binary("busybox"), "sh"], str(binary("busybox-applets"))


class GuardEnvironmentTest(unittest.TestCase):
    def test_config_guard_does_not_receive_the_grafana_setup_variables(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        environment = compose["services"]["config-guard"]["environment"]
        self.assertEqual(sorted(set(SETUP_ONLY_VARIABLES) & set(environment)), [])
        self.assertIn("FARO_API_KEY", environment)

    def test_config_guard_receives_the_checked_retentions(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        environment = compose["services"]["config-guard"]["environment"]
        names = ("TEMPO_RETENTION", "TEMPO_MAX_ACTIVE_SERIES", "ENABLE_EXEMPLARS", "LOKI_RETENTION_PROD", "LOKI_RETENTION_DEFAULT")
        for name in (*names, "PROM_RETENTION_TIME", "PROM_RETENTION_SIZE"):
            with self.subTest(name=name):
                # No default here: guard.sh must see an emptied variable as empty, as on Coolify.
                self.assertEqual(environment.get(name), f"${{{name}:-}}")


if __name__ == "__main__":
    unittest.main()
