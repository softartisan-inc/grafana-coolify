import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from support import ROOT, binary, run

GUARD = ROOT / "config" / "config-guard" / "guard.sh"
VALID_ENV = {
    "IP_HASH_SALT": "harnessSalt0123456789",
    "FARO_API_KEY": "harness-faro-key-0123456789",
    "HOST_MAP": "example.me=guest-front:prod",
    "RESERVED_SUBDOMAINS": "www,api",
    "TENANT_HOST_REGEX": r"^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$",
    "PROJECTS": "demo,other-project",
}


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

    def test_projects_rules(self):
        self.assertEqual(self.guard(PROJECTS="").returncode, 0)
        self.assertEqual(self.guard(PROJECTS="in-immo").returncode, 0)
        for value in ["demo, other", "Demo", "demo,", "a_b", "demo\nDemo Bad"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(PROJECTS=value), "PROJECTS")

    def test_host_map_rules(self):
        self.assertEqual(self.guard(HOST_MAP="").returncode, 0)
        self.assertEqual(self.guard(HOST_MAP="a.me=web:prod,b.me=api:preprod").returncode, 0)
        for value in ["example.me", "Example.me=web:prod", "a.me=web", "a.me=web:prod,", "a.me=web:staging", "a.me=web:prod\nx"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(HOST_MAP=value), "HOST_MAP")

    def test_reserved_subdomains_rules(self):
        self.assertEqual(self.guard(RESERVED_SUBDOMAINS="").returncode, 0)
        for value in ["www,", "WWW", "www api", "www|api", "www\nx y"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(RESERVED_SUBDOMAINS=value), "RESERVED_SUBDOMAINS")

    def test_tenant_regex_needs_sub_group(self):
        self.assertEqual(self.guard(TENANT_HOST_REGEX="").returncode, 0)
        self.assert_fails(self.guard(TENANT_HOST_REGEX=r"^([a-z]+)\.example\.me$"), "TENANT_HOST_REGEX")
        multiline = VALID_ENV["TENANT_HOST_REGEX"] + "\nx"
        self.assert_fails(self.guard(TENANT_HOST_REGEX=multiline), "TENANT_HOST_REGEX")


class DashGuardTest(GuardCases, unittest.TestCase):
    """The POSIX sh of the machine (dash) with its GNU tools."""

    def shell(self):
        return ["sh"], os.environ["PATH"]


class BusyboxGuardTest(GuardCases, unittest.TestCase):
    """busybox sh with busybox applets only on PATH, as in the alpine image of config-guard."""

    def shell(self):
        return [binary("busybox"), "sh"], str(binary("busybox-applets"))


if __name__ == "__main__":
    unittest.main()
