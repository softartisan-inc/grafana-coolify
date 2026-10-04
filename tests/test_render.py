import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402

# Everything Coolify must copy byte for byte: $-expressions, tabs, trailing blank lines,
# non-ASCII text, a line starting with spaces and trailing spaces.
TRICKY = "  first line indented\n$__rate_interval ${DS_X} $$ $1\n\tkey:\tvalue  \nnon-ASCII: é 日本 ✓\n\n\n"

TEMPLATE = """# test template
services:
  config-guard:
    image: @@ALPINE_IMAGE@@
    command: ["sh", "/opt/config-guard/guard.sh"]
    environment:
      CONFIG_GUARD_EXPECTED: "@@CONFIG_GUARD_EXPECTED@@"
      GUARD: "@@GUARD_SHA256@@"
    volumes:
      - type: bind
        source: ./config/config-guard/guard.sh
        target: /opt/config-guard/guard.sh
        content: "@@CONTENT@@"
      - type: bind
        source: ./config
        target: /guard
        read_only: true
  app:
    image: example/app:@@APP_VERSION@@
    command:
      - --config=/etc/app/tricky.txt
      - --other=/etc/app/tricky.txt.bak
    volumes:
      - type: bind
        source: ./config/app/tricky.txt
        target: /etc/app/tricky.txt
        content: "@@CONTENT@@"
      - app-data:/data
volumes:
  app-data:
"""


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config" / "app").mkdir(parents=True)
        (self.root / "config" / "config-guard").mkdir(parents=True)
        (self.root / "config" / "app" / "tricky.txt").write_bytes(TRICKY.encode("utf-8"))
        (self.root / "config" / "config-guard" / "guard.sh").write_text("#!/bin/sh\necho guard\n", encoding="utf-8")
        self.versions = {"ALPINE_IMAGE": "alpine:3.22", "APP_VERSION": "1.2.3"}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, template=TEMPLATE, **kwargs):
        return render.render_text(template, self.root, self.versions, **kwargs)

    def short(self, data):
        return hashlib.sha256(data).hexdigest()[:8]

    def test_content_blocks_are_byte_exact(self):
        doc = yaml.safe_load(self.render())
        app_volume = doc["services"]["app"]["volumes"][0]
        self.assertEqual(app_volume["content"].encode("utf-8"), TRICKY.encode("utf-8"))
        guard_script = doc["services"]["config-guard"]["volumes"][0]
        self.assertEqual(guard_script["content"], "#!/bin/sh\necho guard\n")
        self.assertNotIn("content", doc["services"]["config-guard"]["volumes"][1])

    def test_every_chomping_and_indentation_case_round_trips(self):
        cases = ["x", "x\n", "x\n\n", "\n\nstarts blank\n", " \n  \n", "\tfirst tab\n", "end  \n\n\n", "a\n\n\nb"]
        for text in cases:
            with self.subTest(text=text):
                (self.root / "config" / "app" / "tricky.txt").write_text(text, encoding="utf-8")
                doc = yaml.safe_load(self.render())
                self.assertEqual(doc["services"]["app"]["volumes"][0]["content"], text)

    def test_yaml_structure_is_preserved(self):
        doc = yaml.safe_load(self.render())
        self.assertEqual(doc["services"]["app"]["image"], "example/app:1.2.3")
        self.assertEqual(doc["services"]["app"]["volumes"][1], "app-data:/data")
        self.assertIn("app-data", doc["volumes"])

    def test_paths_are_content_addressed(self):
        short = self.short(TRICKY.encode("utf-8"))
        app = yaml.safe_load(self.render())["services"]["app"]
        self.assertEqual(app["volumes"][0]["source"], f"./config/app/tricky.{short}.txt")
        self.assertEqual(app["volumes"][0]["target"], f"/etc/app/tricky.{short}.txt")
        # References to the target are rewritten in the same service, whole paths only.
        self.assertEqual(app["command"], [f"--config=/etc/app/tricky.{short}.txt", "--other=/etc/app/tricky.txt.bak"])
        guard = yaml.safe_load(self.render())["services"]["config-guard"]
        guard_short = self.short(b"#!/bin/sh\necho guard\n")
        self.assertEqual(guard["command"], ["sh", f"/opt/config-guard/guard.{guard_short}.sh"])

    def test_a_new_content_gets_a_new_name(self):
        before = yaml.safe_load(self.render())["services"]["app"]["volumes"][0]["target"]
        (self.root / "config" / "app" / "tricky.txt").write_text("changed\n", encoding="utf-8")
        after = yaml.safe_load(self.render())["services"]["app"]["volumes"][0]["target"]
        self.assertNotEqual(before, after)
        self.assertEqual(after, "/etc/app/tricky." + self.short(b"changed\n") + ".txt")

    def test_same_target_in_two_services_gets_two_names(self):
        (self.root / "config" / "twin").mkdir()
        (self.root / "config" / "twin" / "tricky.txt").write_text("twin\n", encoding="utf-8")
        twin = TEMPLATE.replace("volumes:\n  app-data:", "").rstrip("\n") + "\n"
        twin = twin.replace("      - app-data:/data\n", "") + (
            "  twin:\n    image: example/app:@@APP_VERSION@@\n    command: [\"--config=/etc/app/tricky.txt\"]\n    volumes:\n"
            "      - type: bind\n        source: ./config/twin/tricky.txt\n        target: /etc/app/tricky.txt\n"
            '        content: "@@CONTENT@@"\n'
        )
        services = yaml.safe_load(self.render(twin))["services"]
        app_target = services["app"]["volumes"][0]["target"]
        twin_target = services["twin"]["volumes"][0]["target"]
        self.assertNotEqual(app_target, twin_target)
        self.assertEqual(services["twin"]["command"], [f"--config={twin_target}"])
        self.assertEqual(services["app"]["command"][0], f"--config={app_target}")

    def test_hashed_path(self):
        digest = "0123456789abcdef" * 4
        self.assertEqual(render.hashed_path("./config/loki/loki.yaml", digest), "./config/loki/loki.01234567.yaml")
        self.assertEqual(render.hashed_path("/etc/alloy/config.alloy", digest), "/etc/alloy/config.01234567.alloy")
        self.assertEqual(render.hashed_path("/opt/tool/run", digest), "/opt/tool/run.01234567")

    def test_output_is_deterministic(self):
        self.assertEqual(self.render(), self.render())

    def test_guard_expectations_cover_the_other_services_files(self):
        env = yaml.safe_load(self.render())["services"]["config-guard"]["environment"]
        tricky_sha = hashlib.sha256(TRICKY.encode("utf-8")).hexdigest()
        self.assertEqual(env["CONFIG_GUARD_EXPECTED"], f"/guard/app/tricky.{tricky_sha[:8]}.txt={tricky_sha}")
        guard_sha = hashlib.sha256(b"#!/bin/sh\necho guard\n").hexdigest()
        self.assertEqual(env["GUARD"], guard_sha)

    def test_strip_content_removes_every_content_line(self):
        text = self.render(strip_content=True)
        self.assertNotIn("content:", text)
        doc = yaml.safe_load(text)
        short = self.short(TRICKY.encode("utf-8"))
        self.assertEqual(doc["services"]["app"]["volumes"][0]["source"], f"./config/app/tricky.{short}.txt")

    def test_unknown_placeholder_is_an_error(self):
        with self.assertRaisesRegex(render.RenderError, "@@NOPE@@"):
            self.render(TEMPLATE.replace("@@APP_VERSION@@", "@@NOPE@@"))

    def test_rejects_unfaithful_files(self):
        bad = {"empty": b"", "cr": b"a\r\nb\n", "bom": "﻿x\n".encode(), "ctrl": b"a\x01b\n", "latin1": b"caf\xe9\n"}
        for name, data in bad.items():
            with self.subTest(name=name):
                (self.root / "config" / "app" / "tricky.txt").write_bytes(data)
                with self.assertRaises(render.RenderError):
                    self.render()

    def test_rejects_source_outside_config(self):
        with self.assertRaisesRegex(render.RenderError, "config"):
            self.render(TEMPLATE.replace("./config/app/tricky.txt", "./secrets.txt"))

    def test_base64_size(self):
        self.assertEqual(render.base64_size("abc"), 4)
        self.assertEqual(render.base64_size("é"), 4)

    def test_grafana_setup_files_are_listed_except_setup(self):
        base = self.root / "config" / "grafana-setup"
        (base / "dashboards").mkdir(parents=True)
        (base / "__pycache__").mkdir()
        (base / "alerting.py").write_text("print('a')\n", encoding="utf-8")
        (base / "setup.py").write_text("print('s')\n", encoding="utf-8")
        (base / "dashboards" / "gc-host.json").write_text("{}\n", encoding="utf-8")
        (base / "notes.txt").write_text("ignored\n", encoding="utf-8")
        (base / "__pycache__" / "setup.cpython-312.py").write_text("ignored\n", encoding="utf-8")
        files = render.grafana_setup_files(self.root)
        self.assertEqual([path for path, _digest in files], ["alerting.py", "dashboards/gc-host.json"])
        self.assertEqual(files[0][1], hashlib.sha256(b"print('a')\n").hexdigest())
        values = render.computed_values(self.root, TEMPLATE)
        self.assertEqual(values["GRAFANA_SETUP_FILES"], ";".join(f"{path}={digest}" for path, digest in files))

    def test_dev_variant_adds_grafana_and_no_content(self):
        dev = render.render_dev(TEMPLATE, self.root, dict(self.versions, GRAFANA_VERSION="13.2.2"))
        self.assertNotIn("content:", dev)
        doc = yaml.safe_load(dev)
        self.assertEqual(doc["services"]["grafana"]["image"], "grafana/grafana:13.2.2")
        # config/ is mounted directly: the repository names, not the content-addressed ones.
        self.assertEqual(doc["services"]["app"]["volumes"][0]["source"], "./config/app/tricky.txt")
        self.assertEqual(doc["services"]["app"]["command"][0], "--config=/etc/app/tricky.txt")


STRIP_TEMPLATE = """services:
  config-guard:
    image: alpine
    environment:
      CONFIG_GUARD_EXPECTED: "@@CONFIG_GUARD_EXPECTED@@"
      GUARD: "@@GUARD_SHA256@@"
    volumes:
      - type: bind
        source: ./config/config-guard/guard.sh
        target: /opt/config-guard/guard.sh
        content: "@@CONTENT@@"
  alloy:
    image: alloy
    volumes:
      - type: bind
        source: ./config/alloy/config.alloy
        target: /etc/alloy/config.alloy
        content: "@@CONTENT@@"
  loki:
    image: loki
    volumes:
      - type: bind
        source: ./config/loki/loki.yaml
        target: /etc/loki/loki.yaml
        content: "@@CONTENT@@"
"""
ALLOY = '// Header comment\n\n  // indented comment\nloki.write "x" {\n  url = "https://loki:3100/push" // end-of-line kept\n\n  // inner\n}\n'
ALLOY_STRIPPED = 'loki.write "x" {\n  url = "https://loki:3100/push" // end-of-line kept\n}\n'
LOKI = "# Loki\n\nserver:\n  # port\n  http_listen_port: 3100 # kept\n\n  url: https://example.com/#anchor\n"
LOKI_STRIPPED = "server:\n  http_listen_port: 3100 # kept\n  url: https://example.com/#anchor\n"


class CommentStrippingTest(unittest.TestCase):
    """Inlined YAML and Alloy files lose full-line comments and blank lines; config/ keeps them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for path, text in {"config-guard/guard.sh": "#!/bin/sh\n\n# guard\necho guard\n", "alloy/config.alloy": ALLOY, "loki/loki.yaml": LOKI}.items():
            (self.root / "config" / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / "config" / path).write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_strip_comments_rules(self):
        self.assertEqual(render.strip_comments(ALLOY, "./config/alloy/config.alloy"), ALLOY_STRIPPED)
        self.assertEqual(render.strip_comments(LOKI, "./config/loki/loki.yaml"), LOKI_STRIPPED)
        self.assertEqual(render.strip_comments(LOKI, "./config/prometheus/prometheus.yml"), LOKI_STRIPPED)
        # Other files (shell, Python) are inlined unchanged: "#!" and "#" lines matter there.
        self.assertEqual(render.strip_comments("#!/bin/sh\n\n# x\n", "./config/config-guard/guard.sh"), "#!/bin/sh\n\n# x\n")
        self.assertEqual(render.strip_comments("# x\n\ny = 1\n", "./config/grafana-setup/setup.py"), "# x\n\ny = 1\n")

    def test_compose_carries_stripped_content_and_hashes(self):
        doc = yaml.safe_load(render.render_text(STRIP_TEMPLATE, self.root, {}))
        services = doc["services"]
        alloy, loki = services["alloy"]["volumes"][0], services["loki"]["volumes"][0]
        self.assertEqual(alloy["content"], ALLOY_STRIPPED)
        self.assertEqual(loki["content"], LOKI_STRIPPED)
        self.assertNotIn("Header comment", alloy["content"])
        self.assertIn("https://loki:3100/push", alloy["content"])
        self.assertIn("https://example.com/#anchor", loki["content"])
        self.assertEqual(services["config-guard"]["volumes"][0]["content"], "#!/bin/sh\n\n# guard\necho guard\n")
        alloy_sha = hashlib.sha256(ALLOY_STRIPPED.encode()).hexdigest()
        loki_sha = hashlib.sha256(LOKI_STRIPPED.encode()).hexdigest()
        self.assertEqual(alloy["source"], f"./config/alloy/config.{alloy_sha[:8]}.alloy")
        expected = services["config-guard"]["environment"]["CONFIG_GUARD_EXPECTED"]
        self.assertEqual(expected, f"/guard/alloy/config.{alloy_sha[:8]}.alloy={alloy_sha};/guard/loki/loki.{loki_sha[:8]}.yaml={loki_sha}")
        # The repository keeps its comments.
        self.assertEqual((self.root / "config/alloy/config.alloy").read_text(encoding="utf-8"), ALLOY)

    def test_dev_variant_expects_the_unstripped_files(self):
        dev = yaml.safe_load(render.render_dev(STRIP_TEMPLATE, self.root, {"GRAFANA_VERSION": "13.2.2"}))
        expected = dev["services"]["config-guard"]["environment"]["CONFIG_GUARD_EXPECTED"]
        alloy_sha = hashlib.sha256(ALLOY.encode()).hexdigest()
        loki_sha = hashlib.sha256(LOKI.encode()).hexdigest()
        self.assertEqual(expected, f"/guard/alloy/config.alloy={alloy_sha};/guard/loki/loki.yaml={loki_sha}")

    def test_repository_configs_keep_their_meaning(self):
        for path in ("loki/loki.yaml", "tempo/tempo.yaml", "prometheus/prometheus.yml"):
            with self.subTest(path=path):
                text = (ROOT / "config" / path).read_text(encoding="utf-8")
                self.assertEqual(yaml.safe_load(render.strip_comments(text, path)), yaml.safe_load(text))
        for path in ("alloy/config.alloy", "alloy-gateway/config.alloy"):
            with self.subTest(path=path):
                text = (ROOT / "config" / path).read_text(encoding="utf-8")
                stripped = render.strip_comments(text, path)
                self.assertLess(len(stripped), len(text))
                kept = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("//")]
                self.assertEqual(stripped.splitlines(), kept)


class RepositoryRenderTest(unittest.TestCase):
    """The committed docker-compose.yaml carries every config file byte for byte, comments stripped (spec 12.2)."""

    def test_every_content_block_matches_its_source(self):
        doc = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
        repository = {item.hashed_source: item.source for item in render.content_items(ROOT, template)}
        seen = 0
        for name, service in doc["services"].items():
            for volume in service.get("volumes", []):
                if isinstance(volume, dict) and "content" in volume:
                    source = repository[volume["source"]]
                    data = render.strip_comments((ROOT / source).read_text(encoding="utf-8"), source).encode("utf-8")
                    short = hashlib.sha256(data).hexdigest()[:8]
                    with self.subTest(service=name, source=volume["source"]):
                        self.assertEqual(volume["content"].encode("utf-8"), data)
                        self.assertIn(f".{short}.", volume["source"])
                        self.assertIn(f".{short}.", volume["target"])
                    seen += 1
        self.assertGreaterEqual(seen, 1)

    def test_header_marks_the_file_as_generated(self):
        self.assertTrue((ROOT / "docker-compose.yaml").read_text(encoding="utf-8").startswith("# GENERATED — DO NOT EDIT"))


if __name__ == "__main__":
    unittest.main()
