import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT

sys.path.insert(0, str(ROOT / "config" / "grafana-setup"))
import dashboards  # noqa: E402


class LoadTest(unittest.TestCase):
    def test_six_dashboards_in_display_order(self):
        self.assertEqual([b["uid"] for b in dashboards.load()], [uid for uid, _project in dashboards.DASHBOARDS])

    def test_missing_or_mismatched_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(dashboards.DashboardError, "gc-project.json"):
                dashboards.load(tmp)
            for uid, _project in dashboards.DASHBOARDS:
                (Path(tmp) / f"{uid}.json").write_text(json.dumps({"uid": uid}), encoding="utf-8")
            (Path(tmp) / "gc-host.json").write_text(json.dumps({"uid": "other"}), encoding="utf-8")
            with self.assertRaisesRegex(dashboards.DashboardError, "gc-host.json: uid 'other'"):
                dashboards.load(tmp)

    def test_normalize_ignores_what_grafana_bumps(self):
        self.assertEqual(dashboards.normalize({"uid": "x", "id": 7, "version": 3, "title": "t"}), {"uid": "x", "title": "t"})


class LinksDashboardTest(unittest.TestCase):
    def test_short_uid(self):
        self.assertEqual(dashboards.short_uid("gcl-", "in-immo"), "gcl-in-immo")
        long_uid = dashboards.short_uid("gcl-", "a" * 64)
        self.assertEqual(len(long_uid), 40)
        self.assertTrue(long_uid.startswith("gcl-aaaa"))
        self.assertNotEqual(long_uid, dashboards.short_uid("gcl-", "a" * 63 + "b"))
        self.assertEqual(dashboards.short_uid("gc-", "b" * 37), "gc-" + "b" * 37)

    def test_links(self):
        board = dashboards.links_dashboard("in-immo", dashboards.load())
        self.assertEqual(board["uid"], "gcl-in-immo")
        urls = [link["url"] for link in board["links"]]
        self.assertEqual(
            urls,
            [
                "/d/gc-project?var-project=in-immo",
                "/d/gc-service?var-project=in-immo",
                "/d/gc-frontend?var-project=in-immo",
                "/d/gc-host",
                "/d/gc-pipeline",
                "/d/gc-cardinality?var-project=in-immo",
            ],
        )
        self.assertIn("(/d/gc-frontend?var-project=in-immo)", board["panels"][0]["options"]["content"])


if __name__ == "__main__":
    unittest.main()
