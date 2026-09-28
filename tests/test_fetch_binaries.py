import unittest

from support import binary, run, versions


class FetchBinariesTest(unittest.TestCase):
    """Every harness binary is installed at the version pinned in tools/versions.env."""

    def assert_version(self, cmd, expected):
        result = run(cmd)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stdout)

    def test_alloy(self):
        self.assert_version([binary("alloy"), "--version"], "version " + versions()["ALLOY_VERSION"])

    def test_loki(self):
        self.assert_version([binary("loki"), "-version"], "version " + versions()["LOKI_VERSION"])

    def test_tempo(self):
        self.assert_version([binary("tempo"), "-version"], "version " + versions()["TEMPO_VERSION"])

    def test_prometheus_and_promtool(self):
        expected = "version " + versions()["PROMETHEUS_VERSION"]
        self.assert_version([binary("prometheus"), "--version"], expected)
        self.assert_version([binary("promtool"), "--version"], expected)

    def test_node_exporter(self):
        self.assert_version([binary("node_exporter"), "--version"], "version " + versions()["NODE_EXPORTER_VERSION"])

    def test_traefik(self):
        self.assert_version([binary("traefik"), "version"], versions()["TRAEFIK_VERSION"].lstrip("v"))

    def test_grafana(self):
        self.assert_version([binary("grafana/bin/grafana"), "--version"], "version " + versions()["GRAFANA_VERSION"])

    def test_ruff(self):
        self.assert_version([binary("ruff"), "--version"], "ruff " + versions()["RUFF_VERSION"])

    def test_busybox(self):
        self.assert_version([binary("busybox"), "--help"], "BusyBox v" + versions()["BUSYBOX_VERSION"])
        self.assert_version([binary("busybox-applets") / "sha256sum", "--help"], "BusyBox v" + versions()["BUSYBOX_VERSION"])

    def test_shellcheck(self):
        self.assert_version([binary("shellcheck"), "--version"], "version: " + versions()["SHELLCHECK_VERSION"].lstrip("v"))


if __name__ == "__main__":
    unittest.main()
