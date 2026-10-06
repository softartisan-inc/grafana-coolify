import contextlib
import hashlib
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
      Y: ${Y:-}
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

    def test_required_syntax_is_refused(self):
        """Coolify turns ${Y:?message} into Y=message instead of refusing to deploy."""
        env = "SERVICE_FQDN_A_80=\nX=1\nY=\nZ=\n"
        for reference in ("${Y:?required}", "${Y?required}", "${Y:?}"):
            with self.subTest(reference=reference):
                template = self.TEMPLATE.replace("${Y:-}", reference)
                errors = check.env_var_mismatches(template, env)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("Y: ${Y:?...} becomes the value of Y under Coolify", errors[0])
        commented = self.TEMPLATE.replace("# comment with ${IGNORED}", "# never ${IGNORED:?x}")
        self.assertEqual(check.env_var_mismatches(commented, env), [])

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


class BundleTest(unittest.TestCase):
    """Spec 4.3: GRAFANA_SETUP_TAG must hold exactly the grafana-setup files of the working tree."""

    TAG = "grafana-setup-content-v1"
    FILES = [("alerting.py", hashlib.sha256(b"new\n").hexdigest())]

    def errors(self, tag=TAG, stored=b"new\n", exists=True):
        shown = []

        def git_show(tag, path):
            shown.append(path)
            return stored

        errors = check.bundle_errors(tag, self.FILES, git_show, lambda _tag: exists)
        self.assertTrue(all(path == "config/grafana-setup/alerting.py" for path in shown), shown)
        return errors

    def test_tag_holding_the_files(self):
        self.assertEqual(self.errors(), [])

    def test_malformed_tag_name(self):
        for tag in ("main", "", "grafana-setup-content-v0", "grafana-setup-content-1", "v1"):
            with self.subTest(tag=tag):
                self.assertIn("must look like grafana-setup-content-v<N>", self.errors(tag=tag)[0])

    def test_absent_tag(self):
        self.assertIn("tag grafana-setup-content-v1 absent", self.errors(exists=False)[0])

    def test_file_changed_after_the_tag(self):
        self.assertIn("differs between tag grafana-setup-content-v1", self.errors(stored=b"old\n")[0])

    def test_file_added_after_the_tag(self):
        self.assertIn("absent from tag grafana-setup-content-v1", self.errors(stored=None)[0])

    def test_shallow_clone_without_the_tag_is_skipped(self):
        """Simulated shallow clone: the skip notice is captured, never printed into the test output."""
        saved = check.git_tag_exists, check.git_is_shallow
        check.git_tag_exists = lambda _tag: False
        try:
            for shallow, expected in ((True, []), (False, 1)):
                with self.subTest(shallow=shallow):
                    check.git_is_shallow = lambda shallow=shallow: shallow
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        errors = check.check_bundle()
                    self.assertEqual(errors if shallow else len(errors), expected)
                    if shallow:
                        self.assertIn("skipped: tag grafana-setup-content-v1 not found locally and this clone is shallow", out.getvalue())
                    else:
                        self.assertEqual(out.getvalue(), "")
        finally:
            check.git_tag_exists, check.git_is_shallow = saved

    def test_repository_tag_is_checked_not_skipped(self):
        """With the tag present locally (any clone depth), the bundle is checked, silently."""
        if not check.git_tag_exists(check.render.load_versions(ROOT / "tools" / "versions.env")["GRAFANA_SETUP_TAG"]):
            self.skipTest("the grafana-setup content tag is not fetched in this clone (git fetch --tags)")
        saved = check.git_is_shallow
        check.git_is_shallow = lambda: True
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                self.assertEqual(check.check_bundle(), [])
        finally:
            check.git_is_shallow = saved
        self.assertEqual(out.getvalue(), "")


class StripHazardTest(unittest.TestCase):
    """strip_comments is line-based: a comment-looking line inside a multi-line value would be cut."""

    def test_yaml_block_scalar_is_flagged(self):
        for line in ("script: |", "script: >-", "  body: |2", "text:   >+  "):
            self.assertTrue(check.strip_hazards("config/x.yaml", f"a: 1\n{line}\n  # kept?\n"), line)

    def test_yaml_plain_values_pass(self):
        text = "a: 1\nexpr: 'x | y'\nurl: https://a/#b\nlist:\n  - '>'\n"
        self.assertEqual(check.strip_hazards("config/x.yml", text), [])

    def test_shell_heredoc_is_flagged(self):
        for line in ("cat <<EOF", "cat <<-'EOF'", 'cat << "END"'):
            errors = check.strip_hazards("config/x.sh", f"#!/bin/sh\n{line}\n# kept?\nEOF\n")
            self.assertEqual(len(errors), 1, line)
            self.assertIn("config/x.sh:2: heredoc", errors[0])

    def test_shell_multiline_quote_is_flagged(self):
        for text in ('a="one\n# two"\n', "a='one\n# two'\n", 'a="x \\" y\n# two"\n'):
            errors = check.strip_hazards("config/x.sh", text)
            self.assertEqual(errors, ["config/x.sh:2: multi-line quoted string, strip_comments is line-based: keep each string on one line"], text)

    def test_shell_continuation_before_comment_is_flagged(self):
        errors = check.strip_hazards("config/x.sh", "#!/bin/sh\nset -- a \\\n  # note\n  b\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("config/x.sh:2: line continuation before a comment line", errors[0])
        # A continuation followed by code is fine.
        self.assertEqual(check.strip_hazards("config/x.sh", "set -- a \\\n  b\n"), [])

    def test_shell_single_line_quotes_and_comments_pass(self):
        text = "#!/bin/sh\n# it's a comment\necho \"it's\" 'a \"b' # don't\nx=${y#z} n=$#\n[ $((1 << 2)) ]\n"
        self.assertEqual(check.strip_hazards("config/x.sh", text), [])

    def test_repository_scripts_have_no_hazard(self):
        for path in ("config/config-guard/guard.sh", "config/prometheus/start.sh"):
            with self.subTest(path=path):
                self.assertEqual(check.strip_hazards(path, (check.ROOT / path).read_text(encoding="utf-8")), [])

    def test_alloy_multiline_raw_string_is_flagged(self):
        errors = check.strip_hazards("config/x.alloy", 'a = `one\n// inside\ntwo`\n')
        self.assertEqual(len(errors), 2)
        self.assertIn("config/x.alloy:1", errors[0])

    def test_alloy_paired_backticks_pass(self):
        self.assertEqual(check.strip_hazards("config/x.alloy", 'a = [`x`, `y` + "z"]\nb = 1\n'), [])

    def test_other_files_are_ignored(self):
        self.assertEqual(check.strip_hazards("config/setup.py", "x = `\ny: |\n"), [])

    def test_repository_inlined_files_pass(self):
        self.assertEqual(check.check_render(), [])


class RepositoryCheckTest(unittest.TestCase):
    def test_check_py_passes_on_the_repository(self):
        result = run([sys.executable, ROOT / "scripts" / "check.py"], timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("[FAIL]", result.stdout)


if __name__ == "__main__":
    unittest.main()
