import contextlib
import io
import sys
import unittest

from support import ROOT, run

sys.path.insert(0, str(ROOT / "scripts"))
import check  # noqa: E402


class SecretScanTest(unittest.TestCase):
    def findings(self, text):
        return check.find_hardcoded_secrets(text, "f")

    def test_literal_values_are_flagged(self):
        for line in [
            "password: hunter2",
            'api_key = "abc"',
            "  token: 'glsa_abc123'",
            "FARO_API_KEY=k3yValue123",
            "ip_hash_salt: s4ltValue99",
            '"secret": "x"',
            "password: hunter",
            "FARO_API_KEY=abcdefghijklmnop",
            "  client_secret: supersecret",
        ]:
            with self.subTest(line=line):
                self.assertEqual(len(self.findings(line)), 1)

    def test_references_and_code_are_not_flagged(self):
        for line in [
            'api_key = sys.env("FARO_API_KEY")',
            "password: ${GRAFANA_PASSWORD}",
            'token = os.environ.get("GRAFANA_SA_TOKEN", "")',
            "self.token = token",
            '"tags": [{"key": "service.name", "value": "service"}],',
            '`keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,',
            "# password: hunter2 (comment)",
            "// api_key = \"abc\" (comment)",
            "token:",
            "skip_token_check: false",
            "api_key = faro_key",
            'if ! matches "${IP_HASH_SALT:-}" \'^[A-Za-z0-9]{16,}$\'; then',
        ]:
            with self.subTest(line=line):
                self.assertEqual(self.findings(line), [])


class EnvVarTest(unittest.TestCase):
    TEMPLATE = """# comment with ${IGNORED}
services:
  a:
    environment:
      SERVICE_FQDN_A_80:
      X: ${X:-1}
      Y: ${Y:?required}
    command: ["--flag=${Z}"]
"""

    def test_exact_match(self):
        env = "SERVICE_FQDN_A_80=\nX=1\nY=\nZ=\nALLOY_INTERNAL_URL=\n"
        self.assertEqual(check.env_var_mismatches(self.TEMPLATE, env), [])

    def test_missing_and_unused(self):
        errors = check.env_var_mismatches(self.TEMPLATE, "X=1\nY=\nZ=\nEXTRA=\n")
        self.assertIn("SERVICE_FQDN_A_80: used in compose.template.yaml but missing from .env.example", errors)
        self.assertIn("EXTRA: in .env.example but unused by compose.template.yaml", errors)
        self.assertFalse(any("IGNORED" in e for e in errors))

    def test_comments_in_env_example_are_ignored(self):
        env = "# X=commented\nSERVICE_FQDN_A_80=\nX=1\nY=\nZ=\n"
        self.assertEqual(check.env_var_mismatches(self.TEMPLATE, env), [])


class PortsTest(unittest.TestCase):
    def test_ports_are_refused(self):
        compose = {"services": {"loki": {"expose": ["3100"]}, "bad": {"ports": [{"target": 3100, "published": "3100"}]}}}
        self.assertEqual(len(check.port_violations(compose)), 1)
        self.assertEqual(check.port_violations({"services": {"loki": {"expose": ["3100"]}}}), [])


class LimitsTest(unittest.TestCase):
    def test_every_service_needs_mem_limit_and_cpus(self):
        compose = {"services": {"ok": {"mem_limit": "67108864", "cpus": 0.2}, "no-cpus": {"mem_limit": "1"}, "none": {}}}
        self.assertEqual(check.limit_violations(compose), ["no-cpus: cpus missing", "none: mem_limit missing", "none: cpus missing"])


class TargetsTest(unittest.TestCase):
    def test_a_content_target_is_used_once(self):
        mounts = [("alloy", "/etc/alloy/config.1.alloy"), ("alloy-gateway", "/etc/alloy/config.1.alloy"), ("loki", "/etc/loki/loki.2.yaml")]
        self.assertEqual(check.target_collisions(mounts), ["/etc/alloy/config.1.alloy: content target of alloy, alloy-gateway"])
        self.assertEqual(check.target_collisions(mounts[1:]), [])

    def test_size_warns_under_4_kib_of_margin(self):
        budget = check.render.BUDGET_BYTES
        cases = ((budget - 4096, False), (budget - 4095, True), (budget, True))
        for size, warned in cases:
            with self.subTest(size=size), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(check.check_size(size), [])
                self.assertEqual("WARN" in out.getvalue(), warned, out.getvalue())
        self.assertEqual(check.check_size(budget + 1), [f"docker-compose.yaml is {budget + 1} bytes in base64, budget is {budget}"])


class RepositoryCheckTest(unittest.TestCase):
    def test_check_py_passes_on_the_repository(self):
        result = run([sys.executable, ROOT / "scripts" / "check.py"], timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("[FAIL]", result.stdout)


if __name__ == "__main__":
    unittest.main()
