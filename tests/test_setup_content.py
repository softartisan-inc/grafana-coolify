import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT

SETUP = ROOT / "config" / "grafana-setup" / "setup.py"
sys.path.insert(0, str(SETUP.parent))
sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402
import setup  # noqa: E402

FILES = {"alerting.py": "VALUE = 1\n", "dashboards.py": "VALUE = 2\n", "dashboards/gc-host.json": "{}\n"}


class ContentDownloadTest(unittest.TestCase):
    """setup.py writes the plan B content only when every file matches its pinned SHA-256."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mirror = Path(self.tmp.name) / "mirror"
        self.target = Path(self.tmp.name) / "target"
        for path, text in FILES.items():
            (self.mirror / "config/grafana-setup" / path).parent.mkdir(parents=True, exist_ok=True)
            (self.mirror / "config/grafana-setup" / path).write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def listing(self, **replace):
        digests = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in FILES.items()}
        digests.update(replace)
        return ";".join(f"{path}={digest}" for path, digest in sorted(digests.items()))

    def download(self, listing=None):
        files = setup.parse_content(self.listing() if listing is None else listing)
        setup.download(setup.content_url({"GRAFANA_SETUP_PINNED_URL": f"file:{self.mirror}"}), files, self.target, pause=0)

    def test_verified_files_are_written(self):
        self.download()
        for path, text in FILES.items():
            with self.subTest(path=path):
                self.assertEqual((self.target / path).read_text(encoding="utf-8"), text)

    def test_tampered_file_is_refused(self):
        (self.mirror / "config/grafana-setup/alerting.py").write_text("VALUE = 666\n", encoding="utf-8")
        with self.assertRaisesRegex(setup.SetupError, r"alerting.py: SHA-256 .* nothing was run"):
            self.download()

    def test_missing_file(self):
        (self.mirror / "config/grafana-setup/dashboards/gc-host.json").unlink()
        with self.assertRaisesRegex(setup.SetupError, "gc-host.json"):
            self.download()

    def test_malformed_listings_are_refused(self):
        digest = "0" * 64
        for listing in (
            "",
            f"alerting.py={digest}",
            f"../../etc/passwd={digest};alerting.py={digest};dashboards.py={digest}",
            f"/etc/passwd={digest};alerting.py={digest};dashboards.py={digest}",
            f"alerting.py=abc;dashboards.py={digest}",
            f"alerting.py={digest};dashboards.py={digest};run.sh={digest}",
            f"setup.py={digest};alerting.py={digest};dashboards.py={digest};Evil.py={digest}",
        ):
            with self.subTest(listing=listing):
                with self.assertRaises(setup.SetupError):
                    setup.parse_content(listing)

    def test_mirror_replaces_the_pinned_url(self):
        env = {"GRAFANA_SETUP_PINNED_URL": "https://raw.githubusercontent.com/o/r/grafana-setup-content-v1", "GRAFANA_SETUP_MIRROR_URL": "file:/srv/mirror/"}
        self.assertEqual(setup.content_url(env), "file:/srv/mirror/config/grafana-setup")
        self.assertEqual(setup.content_url(dict(env, GRAFANA_SETUP_MIRROR_URL="")), "https://raw.githubusercontent.com/o/r/grafana-setup-content-v1/config/grafana-setup")

    def test_unsupported_scheme(self):
        with self.assertRaisesRegex(setup.SetupError, "must start with one of"):
            setup.content_url({"GRAFANA_SETUP_PINNED_URL": "ftp://example.com/x"})

    def test_read_is_bounded(self):
        big = self.mirror / "big.json"
        big.write_bytes(b"x" * 101)
        with self.assertRaisesRegex(setup.SetupError, "larger than 100 bytes"):
            setup.fetch(f"file:{big}", limit=100, pause=0)
        self.assertEqual(len(setup.fetch(f"file:{big}", limit=101, pause=0)), 101)
        self.assertEqual(setup.MAX_CONTENT_BYTES, 2 * 1024 * 1024)

    def test_network_errors_are_retried_then_reported(self):
        calls = []

        def failing(url, timeout):
            calls.append(url)
            raise setup.urllib.error.URLError("unreachable")

        original = setup.urllib.request.urlopen
        setup.urllib.request.urlopen = failing
        try:
            with self.assertRaisesRegex(setup.SetupError, "unreachable"):
                setup.fetch("https://example.invalid/x", attempts=3, pause=0)
        finally:
            setup.urllib.request.urlopen = original
        self.assertEqual(len(calls), 3)


    def test_a_directory_shadowing_a_module_is_refused(self):
        # dashboards/ (JSON) imports as a namespace package when dashboards.py is absent.
        def partial(base_url, files, target, pause=2):
            for path in ("alerting.py", "dashboards/gc-host.json"):
                (Path(target) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(target) / path).write_text(FILES[path], encoding="utf-8")

        saved = {name: sys.modules.pop(name, None) for name in ("alerting", "dashboards")}
        # As in the container: the repository modules are not importable, only the download is.
        saved_path = sys.path[:]
        sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != SETUP.parent]
        original = setup.download
        setup.download = partial
        try:
            with self.assertRaisesRegex(setup.SetupError, "dashboards.py is missing"):
                env = {"GRAFANA_SETUP_FILES": self.listing(), "GRAFANA_SETUP_MIRROR_URL": f"file:{self.mirror}"}
                setup.provision_content(None, env, [], lambda _line: None, pause=0)
        finally:
            setup.download = original
            sys.path[:] = saved_path
            for name, module in saved.items():
                sys.modules.pop(name, None)
                if module is not None:
                    sys.modules[name] = module


class RenderedContentTest(unittest.TestCase):
    """The deployed compose inlines setup.py and pins every other grafana-setup file."""

    def setUp(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        self.service = compose["services"]["grafana-setup"]
        self.env = self.service["environment"]

    def test_listing_matches_the_repository(self):
        expected = ";".join(f"{path}={digest}" for path, digest in render.grafana_setup_files(ROOT))
        self.assertEqual(self.env["GRAFANA_SETUP_FILES"], expected)
        paths = [path for path, _digest in setup.parse_content(self.env["GRAFANA_SETUP_FILES"])]
        self.assertEqual([p for p in paths if p.endswith(".py")], ["alerting.py", "dashboards.py"])
        self.assertIn("dashboards/gc-host.json", paths)
        self.assertNotIn("setup.py", paths)

    def test_pinned_url_uses_the_tag(self):
        tag = render.load_versions(ROOT / "tools" / "versions.env")["GRAFANA_SETUP_TAG"]
        self.assertEqual(self.env["GRAFANA_SETUP_PINNED_URL"], f"https://raw.githubusercontent.com/softartisan-inc/grafana-coolify/{tag}")

    def test_only_setup_is_inlined(self):
        contents = [volume for volume in self.service["volumes"] if isinstance(volume, dict) and "content" in volume]
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["content"], SETUP.read_text(encoding="utf-8"))

    def test_repository_files_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = setup.parse_content(self.env["GRAFANA_SETUP_FILES"])
            setup.download(setup.content_url({"GRAFANA_SETUP_MIRROR_URL": f"file:{ROOT}"}), files, tmp, pause=0)
            self.assertTrue((Path(tmp) / "dashboards" / "gc-project.json").is_file())


if __name__ == "__main__":
    unittest.main()
