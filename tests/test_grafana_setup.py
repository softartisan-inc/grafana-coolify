import ast
import contextlib
import copy
import http.client
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from support import ROOT, require_harness, run

SETUP_DIR = ROOT / "config" / "grafana-setup"
SETUP = SETUP_DIR / "setup.py"
TOKEN = "glsa_testTokenValue_0123456789abcdef"
BOT_TOKEN = "123456:fakeBotToken_abcdefghijklmnop"
sys.path.insert(0, str(SETUP_DIR))
sys.path.insert(0, str(ROOT / "scripts"))
import dashboards  # noqa: E402
import render  # noqa: E402
import setup  # noqa: E402

PROVISIONING = "/api/v1/provisioning"


class FakeGrafana(BaseHTTPRequestHandler):
    """In-memory Grafana API: health, datasources, folders, dashboards and legacy alerting provisioning.

    Reproduces the behaviours grafana-setup depends on (checked on Grafana 13.2.2): the first
    datasource becomes the default one, UIDs are limited to 40 characters, a dashboard write bumps
    `version`, contact points are listed with their secrets only by the export endpoint (which has
    no contactPoints key while there is none), the default policy routes to the built-in receiver
    "empty" (no integration: nobody is notified), a missing alert rule answers 404 with a non-JSON
    body.
    """

    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length)) if length else None

    def handle_any(self, method):
        state = self.state
        path = self.path
        state["requests"].append((method, path, self.headers.get("X-Disable-Provenance")))
        if path == "/api/health":
            return self.reply(200, {"database": "ok", "version": state["version"]})
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply(401, {"message": "invalid API key"})
        if state.get("fail_with_token_echo"):
            return self.reply(500, {"message": "internal error for " + self.headers["Authorization"]})
        if path.startswith(PROVISIONING):
            if state.get("legacy_gone"):
                return self.reply(410, {"message": "This endpoint has been removed."})
            return self.provisioning(method, path[len(PROVISIONING) :])
        if path.startswith("/api/datasources/uid/"):
            uid = path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["datasources"][uid]) if uid in state["datasources"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["datasources"][uid] = self.body()
                return self.reply(200, {"message": "updated"})
        if path == "/api/datasources" and method == "POST":
            data = self.body()
            data.setdefault("isDefault", not state["datasources"])
            state["datasources"][data["uid"]] = data
            return self.reply(200, {"message": "created"})
        if path.startswith("/api/folders/"):
            uid = path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["folders"][uid]) if uid in state["folders"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["folders"][uid].update(title=self.body()["title"])
                return self.reply(200, state["folders"][uid])
        if path == "/api/folders" and method == "POST":
            data = self.body()
            if len(data["uid"]) > 40:
                return self.reply(400, {"message": "uid too long, max 40 characters"})
            state["folders"][data["uid"]] = data
            return self.reply(200, data)
        if path.startswith("/api/dashboards/uid/") and method == "GET":
            uid = path.rsplit("/", 1)[1]
            return self.reply(200, state["dashboards"][uid]) if uid in state["dashboards"] else self.reply(404, {"message": "Dashboard not found"})
        if path == "/api/dashboards/db" and method == "POST":
            data = self.body()
            dashboard = data["dashboard"]
            if len(dashboard["uid"]) > 40 or data["folderUid"] not in state["folders"]:
                return self.reply(400, {"message": "invalid uid or folder"})
            previous = state["dashboards"].get(dashboard["uid"], {}).get("dashboard", {})
            stored = dict(dashboard, id=7, version=previous.get("version", 0) + 1)
            state["dashboards"][dashboard["uid"]] = {"meta": {"folderUid": data["folderUid"]}, "dashboard": stored}
            return self.reply(200, {"status": "success", "uid": dashboard["uid"], "version": stored["version"]})
        return self.reply(404, {"message": "unknown route"})

    def provisioning(self, method, path):
        state = self.state
        if path.startswith("/contact-points/export") and method == "GET":
            fields = ("uid", "type", "settings", "disableResolveMessage")
            points = [{"name": p["name"], "receivers": [{k: p[k] for k in fields}]} for p in state["contact_points"].values()]
            return self.reply(200, {"apiVersion": 1, "contactPoints": points} if points else {"apiVersion": 1})
        if path == "/contact-points" and method == "POST":
            data = self.body()
            if state.get("echo_settings_error"):
                return self.reply(400, {"message": f"invalid settings: {json.dumps(data['settings'])}"})
            state["contact_points"][data["uid"]] = data
            return self.reply(202, data)
        if path.startswith("/contact-points/") and method == "PUT":
            data = self.body()
            state["contact_points"][path.rsplit("/", 1)[1]] = data
            return self.reply(202, {"message": "contactpoint updated"})
        if path == "/policies":
            if method == "GET":
                return self.reply(200, state["policy"])
            if method == "PUT":
                data = self.body()
                receivers = {route["receiver"] for route in [data, *data.get("routes", [])]}
                if not receivers <= {*state["contact_points"], "empty"}:
                    return self.reply(400, {"message": "receiver does not exist"})
                for route in data.get("routes", []):
                    route["object_matchers"] = sorted(route.get("object_matchers", []))
                state["policy"] = data
                return self.reply(202, {"message": "policies updated"})
        if path.startswith("/alert-rules/"):
            uid = path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["rules"][uid]) if uid in state["rules"] else self.reply(404, b"")
            if method == "PUT":
                state["rules"][uid] = dict(self.body(), id=1, updated="2026-10-01T00:00:00Z")
                return self.reply(200, state["rules"][uid])
        if path == "/alert-rules" and method == "POST":
            data = self.body()
            state["rules"][data["uid"]] = dict({"keep_firing_for": "0s"}, **data, id=1, updated="2026-10-01T00:00:00Z")
            return self.reply(201, state["rules"][data["uid"]])
        return self.reply(404, {"message": "unknown provisioning route"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")


def new_state():
    return {
        "version": "13.2.2",
        "datasources": {},
        "folders": {},
        "dashboards": {},
        "contact_points": {},
        "policy": {"receiver": "empty", "group_by": ["grafana_folder", "alertname"]},
        "rules": {},
        "requests": [],
    }


class GrafanaSetupUnitTest(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        handler = type("Handler", (FakeGrafana,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {
            "PATH": os.environ["PATH"],
            "GRAFANA_URL": f"http://127.0.0.1:{self.server.server_port}",
            "GRAFANA_SA_TOKEN": TOKEN,
            "LOKI_INTERNAL_URL": "http://loki-abc:3100",
            "TEMPO_INTERNAL_URL": "http://tempo-abc:3200",
            "PROMETHEUS_INTERNAL_URL": "http://prometheus-abc:9090",
            "PROJECTS": "demo, other-project,demo",
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_CHAT_ID": "-1001234567890",
            "ALERT_EMAILS": "ops@example.com, dev@example.com",
            # Plan B content: the working files, checked against the hashes render.py computes.
            "GRAFANA_SETUP_FILES": ";".join(f"{path}={digest}" for path, digest in render.grafana_setup_files(ROOT)),
            "GRAFANA_SETUP_MIRROR_URL": f"file:{ROOT}",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def setup_run(self, **overrides):
        return run([sys.executable, SETUP], env=dict(self.env, **overrides), timeout=60)

    def writes(self):
        return [r for r in self.state["requests"] if r[0] in ("POST", "PUT")]

    def test_first_run_creates_everything(self):
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sorted(self.state["datasources"]), ["gc-loki", "gc-prometheus", "gc-tempo"])
        self.assertEqual(sorted(self.state["folders"]), ["gc-demo", "gc-grafana-coolify", "gc-other-project"])
        self.assertEqual(self.state["datasources"]["gc-loki"]["url"], "http://loki-abc:3100")
        expected_boards = sorted([uid for uid, _p in dashboards.DASHBOARDS] + ["gcl-demo", "gcl-other-project"])
        self.assertEqual(sorted(self.state["dashboards"]), expected_boards)
        self.assertEqual(self.state["dashboards"]["gc-host"]["meta"]["folderUid"], "gc-grafana-coolify")
        self.assertEqual(self.state["dashboards"]["gcl-demo"]["meta"]["folderUid"], "gc-demo")
        self.assertEqual(sorted(self.state["contact_points"]), ["gc-email", "gc-telegram"])
        self.assertEqual(sorted(self.state["rules"]), ["gc-cardinality", "gc-disk", "gc-error-rate", "gc-latency", "gc-rejections", "gc-silence"])

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.setup_run().returncode, 0)
        snapshot = json.dumps({k: v for k, v in self.state.items() if k != "requests"}, sort_keys=True)
        self.state["requests"].clear()
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.dumps({k: v for k, v in self.state.items() if k != "requests"}, sort_keys=True), snapshot)
        self.assertEqual(self.writes(), [])
        # 3 datasources, 3 folders, 8 dashboards, 2 contact points, the policy, 6 rules.
        self.assertEqual(result.stdout.count(": unchanged"), 23)

    def test_changed_url_is_updated_and_default_flag_kept(self):
        self.assertEqual(self.setup_run().returncode, 0)
        self.assertTrue(self.state["datasources"]["gc-loki"]["isDefault"])
        result = self.setup_run(LOKI_INTERNAL_URL="http://loki-new:3100")
        self.assertIn("datasource gc-loki: updated", result.stdout)
        self.assertEqual(self.state["datasources"]["gc-loki"]["url"], "http://loki-new:3100")
        self.assertTrue(self.state["datasources"]["gc-loki"]["isDefault"])

    def test_correlations(self):
        self.assertEqual(self.setup_run().returncode, 0)
        loki = self.state["datasources"]["gc-loki"]["jsonData"]["derivedFields"][0]
        self.assertEqual((loki["matcherType"], loki["matcherRegex"], loki["datasourceUid"]), ("label", "trace_id", "gc-tempo"))
        tempo = self.state["datasources"]["gc-tempo"]["jsonData"]
        self.assertEqual(tempo["tracesToLogsV2"]["datasourceUid"], "gc-loki")
        self.assertEqual(tempo["tracesToLogsV2"]["query"], '{project=~".+"} | trace_id="${__trace.traceId}"')
        self.assertEqual(tempo["tracesToMetrics"]["datasourceUid"], "gc-prometheus")

    def test_old_grafana_stops_cleanly(self):
        self.state["version"] = "11.6.3"
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Grafana 11.6.3 is not supported", result.stdout)
        self.assertEqual([r for r in self.state["requests"] if r[1] != "/api/health"], [])

    def test_token_never_printed(self):
        self.state["fail_with_token_echo"] = True
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(TOKEN, result.stdout)
        self.assertIn("***", result.stdout)

    def test_bot_token_never_printed(self):
        self.state["echo_settings_error"] = True
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid settings", result.stdout)
        self.assertNotIn(BOT_TOKEN, result.stdout)
        self.assertIn("***", result.stdout)

    def test_invalid_project_name(self):
        result = self.setup_run(PROJECTS="ok,Not_Valid")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid project name", result.stdout)

    def test_project_rule_matches_config_guard(self):
        """Same rule as config-guard: ^[a-z0-9][a-z0-9-]{0,63}$ per item."""
        self.assertEqual(self.setup_run(PROJECTS="a" * 64).returncode, 0)
        for value in ("-leading", "a" * 65):
            with self.subTest(value=value):
                result = self.setup_run(PROJECTS=f"ok,{value}")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("invalid project name", result.stdout)

    def test_long_project_names_get_uids_within_40_characters(self):
        result = self.setup_run(PROJECTS="a" * 64)
        self.assertEqual(result.returncode, 0, result.stdout)
        uid = setup.folder_uid("a" * 64)
        self.assertLessEqual(len(uid), 40)
        self.assertIn(uid, self.state["folders"])
        self.assertIn(dashboards.short_uid("gcl-", "a" * 64), self.state["dashboards"])

    def test_missing_variable(self):
        result = self.setup_run(TEMPO_INTERNAL_URL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TEMPO_INTERNAL_URL is required", result.stdout)

    def test_grafana_url_must_be_http(self):
        """Coolify turned `${GRAFANA_URL:?GRAFANA_URL is required}` into the value of the variable."""
        for value in ("GRAFANA_URL is required", "grafana:3000", "ftp://grafana:3000", "http://", "http://a b"):
            with self.subTest(value=value):
                result = self.setup_run(GRAFANA_URL=value)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("GRAFANA_URL must be an http:// or https:// URL", result.stdout)
                self.assertNotIn("Traceback", result.stdout)
        self.assertEqual(self.state["requests"], [])

    def test_internal_urls_must_be_http(self):
        for name in ("LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL"):
            with self.subTest(name=name):
                result = self.setup_run(**{name: f"{name} is required"})
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"{name} must be an http:// or https:// URL", result.stdout)
                self.assertNotIn("Traceback", result.stdout)

    def test_rejected_url_never_prints_its_credentials(self):
        for name in ("GRAFANA_URL", "LOKI_INTERNAL_URL"):
            with self.subTest(name=name):
                result = self.setup_run(**{name: "https://user:s3cretPass@host name"})
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"{name} must be an http:// or https:// URL", result.stdout)
                self.assertIn("https://***@host name", result.stdout)
                self.assertNotIn("s3cretPass", result.stdout)
                self.assertNotIn("user:", result.stdout)

    def test_invalid_url_stops_cleanly_without_retrying(self):
        """http.client raises InvalidURL on a malformed port: a clean stop, at once."""
        result = self.setup_run(GRAFANA_URL="http://127.0.0.1:notaport")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("grafana-setup: ERROR: GET /api/health: invalid request", result.stdout)
        self.assertNotIn("Traceback", result.stdout)

    def test_parse_projects(self):
        self.assertEqual(setup.parse_projects(" a, b ,a,,"), ["a", "b"])
        self.assertEqual(setup.parse_projects(""), [])

    # ------------------------------------------------------------------ plan B
    def test_every_alerting_write_disables_provenance(self):
        self.assertEqual(self.setup_run().returncode, 0)
        alerting_writes = [r for r in self.writes() if r[1].startswith(PROVISIONING)]
        self.assertEqual(len(alerting_writes), 9)
        self.assertEqual({r[2] for r in alerting_writes}, {"true"})

    def test_policy_routes_critical_prod_to_telegram_and_keeps_manual_routes(self):
        webhook = {"uid": "team-sms", "name": "team-sms", "type": "webhook", "settings": {"url": "http://x"}, "disableResolveMessage": False}
        self.state["contact_points"]["team-sms"] = webhook
        manual = {"receiver": "team-sms", "object_matchers": [["team", "=", "billing"]]}
        self.state["policy"]["routes"] = [manual]
        self.assertEqual(self.setup_run().returncode, 0)
        policy = self.state["policy"]
        self.assertEqual(policy["receiver"], "gc-email")
        self.assertEqual(policy["routes"], [{"receiver": "gc-telegram", "object_matchers": [["env", "=", "prod"], ["severity", "=", "critical"]]}, manual])
        self.assertIn("notification policy: unchanged", self.setup_run().stdout)

    def test_settings_added_by_hand_to_our_route_are_kept(self):
        self.assertEqual(self.setup_run().returncode, 0)
        route = self.state["policy"]["routes"][0]
        route.update({"continue": True, "group_wait": "10s", "mute_time_intervals": ["nights"]})
        result = self.setup_run()
        self.assertIn("notification policy: unchanged", result.stdout)
        self.assertEqual(self.state["policy"]["routes"][0]["group_wait"], "10s")
        self.assertTrue(self.state["policy"]["routes"][0]["continue"])

    def test_telegram_token_change_updates_the_contact_point(self):
        self.assertEqual(self.setup_run().returncode, 0)
        result = self.setup_run(TELEGRAM_BOT_TOKEN="654321:anotherFakeBotToken_zyx")
        self.assertIn("contact point gc-telegram: updated", result.stdout)
        self.assertIn("contact point gc-email: unchanged", result.stdout)
        self.assertEqual(self.state["contact_points"]["gc-telegram"]["settings"]["bottoken"], "654321:anotherFakeBotToken_zyx")

    def test_emails_are_joined_for_grafana(self):
        self.assertEqual(self.setup_run().returncode, 0)
        self.assertEqual(self.state["contact_points"]["gc-email"]["settings"]["addresses"], "ops@example.com;dev@example.com")

    def test_threshold_change_updates_only_that_rule(self):
        self.assertEqual(self.setup_run().returncode, 0)
        result = self.setup_run(ALERT_P95_MS="900")
        self.assertIn("alert rule gc-latency: updated", result.stdout)
        self.assertEqual(result.stdout.count("alert rule gc-"), 6)
        self.assertEqual(result.stdout.count(": updated"), 1)
        condition = self.state["rules"]["gc-latency"]["data"][1]["model"]["conditions"][0]["evaluator"]
        self.assertEqual(condition, {"type": "gt", "params": [900]})

    def test_without_emails_the_rules_notify_nobody(self):
        result = self.setup_run(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", ALERT_EMAILS="")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("notifications: skipped (ALERT_EMAILS is empty: the rules notify nobody)", result.stdout)
        self.assertEqual(self.state["contact_points"], {})
        self.assertEqual(self.state["policy"]["receiver"], "empty")
        self.assertEqual(len(self.state["rules"]), 6)

    def test_email_only_has_no_telegram_route(self):
        self.assertEqual(self.setup_run(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="").returncode, 0)
        self.assertEqual(sorted(self.state["contact_points"]), ["gc-email"])
        self.assertEqual(self.state["policy"], {"receiver": "gc-email", "group_by": ["grafana_folder", "alertname"]})

    def test_invalid_alerting_variables_stop_before_any_plan_b_write(self):
        cases = {
            "ALERT_ERROR_RATE='5%' is not a valid float": {"ALERT_ERROR_RATE": "5%"},
            "ALERT_ERROR_RATE='2' is out of range": {"ALERT_ERROR_RATE": "2"},
            "ALERT_SILENCE_MIN='0' is out of range": {"ALERT_SILENCE_MIN": "0"},
            "ALERT_DISK_PCT='100' is out of range": {"ALERT_DISK_PCT": "100"},
            "ALERT_P95_MIN_CALLS='0' is out of range": {"ALERT_P95_MIN_CALLS": "0"},
            "go together": {"TELEGRAM_CHAT_ID": ""},
            "ALERT_EMAILS is required when Telegram is set": {"ALERT_EMAILS": ""},
            "ALERT_EMAILS: invalid address": {"ALERT_EMAILS": "ops@example.com,not-an-email"},
            "TELEGRAM_CHAT_ID='my group'": {"TELEGRAM_CHAT_ID": "my group"},
            "HOST_ENV='staging' must be prod or preprod": {"HOST_ENV": "staging"},
        }
        for message, overrides in cases.items():
            with self.subTest(message=message):
                self.state["requests"].clear()
                result = self.setup_run(**overrides)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stdout)
                self.assertIn("datasources and folders are provisioned, dashboards and alerts are not", result.stdout)
                plan_b = [r for r in self.writes() if not r[1].startswith(("/api/datasources", "/api/folders"))]
                self.assertEqual(plan_b, [])
                self.assertNotIn(BOT_TOKEN, result.stdout)

    def test_content_download_failure_comes_after_plan_a(self):
        files = dict(item.split("=") for item in self.env["GRAFANA_SETUP_FILES"].split(";"))
        tampered = ";".join(f"{path}={'0' * 64 if path == 'dashboards.py' else digest}" for path, digest in files.items())
        for overrides, message in (
            ({"GRAFANA_SETUP_MIRROR_URL": "file:/nonexistent"}, "No such file or directory"),
            ({"GRAFANA_SETUP_FILES": tampered}, "dashboards.py: SHA-256"),
            ({"GRAFANA_SETUP_FILES": ""}, "GRAFANA_SETUP_FILES does not list alerting.py, dashboards.py"),
        ):
            with self.subTest(message=message):
                result = self.setup_run(**overrides)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("grafana-setup: datasources and folders: done", result.stdout)
                self.assertIn("datasources and folders are provisioned, dashboards and alerts are not", result.stdout)
                self.assertIn(message, result.stdout)
                self.assertNotIn("grafana-setup: done", result.stdout)
                self.assertEqual(sorted(self.state["datasources"]), ["gc-loki", "gc-prometheus", "gc-tempo"])
                self.assertEqual(self.state["dashboards"], {})

    def test_folder_uid_matches_the_dashboards_rule(self):
        for project in ("demo", "b" * 37, "b" * 38, "a" * 64):
            with self.subTest(project=project):
                self.assertEqual(setup.folder_uid(project), dashboards.short_uid("gc-", project))

    def test_dashboard_version_bump_is_not_a_change(self):
        self.assertEqual(self.setup_run().returncode, 0)
        self.state["dashboards"]["gc-host"]["dashboard"]["version"] = 42
        self.assertIn("dashboard gc-host: unchanged", self.setup_run().stdout)

    def test_dashboard_edited_in_grafana_is_reimported(self):
        self.assertEqual(self.setup_run().returncode, 0)
        self.state["dashboards"]["gc-host"]["dashboard"]["title"] = "Edited by hand"
        result = self.setup_run()
        self.assertIn("dashboard gc-host: updated", result.stdout)
        self.assertEqual(self.state["dashboards"]["gc-host"]["dashboard"]["title"], "Hôte")

    def test_legacy_alerting_api_gone_is_explained(self):
        self.state["legacy_gone"] = True
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("legacy alerting provisioning API unavailable", result.stdout)

    def test_links_dashboard_sets_the_project_variable(self):
        self.assertEqual(self.setup_run().returncode, 0)
        links = {link["title"]: link["url"] for link in self.state["dashboards"]["gcl-demo"]["dashboard"]["links"]}
        self.assertEqual(links["Vue projet"], "/d/gc-project?var-project=demo")
        self.assertEqual(links["Hôte"], "/d/gc-host")
        self.assertEqual(len(links), 6)


class GrafanaRequestErrorTest(unittest.TestCase):
    """Grafana.request turns every transport failure into a SetupError: no raw traceback."""

    def request_raising(self, exc):
        def urlopen(*_args, **_kwargs):
            raise exc

        saved = setup.urllib.request.urlopen
        setup.urllib.request.urlopen = urlopen
        try:
            return setup.Grafana("http://grafana:3000", TOKEN).request("GET", "/api/health")
        finally:
            setup.urllib.request.urlopen = saved

    def test_value_error_is_a_non_retryable_setup_error(self):
        with self.assertRaises(setup.InvalidRequest) as caught:
            self.request_raising(ValueError(f"unknown url type: {TOKEN}"))
        self.assertIsInstance(caught.exception, setup.SetupError)
        self.assertIn("GET /api/health: invalid request", str(caught.exception))

    def test_http_exception_is_a_setup_error(self):
        for exc in (http.client.BadStatusLine("garbage"), http.client.IncompleteRead(b"x"), http.client.RemoteDisconnected("closed")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(setup.SetupError) as caught:
                    self.request_raising(exc)
                self.assertNotIsInstance(caught.exception, setup.InvalidRequest)
                self.assertIn("Grafana unreachable", str(caught.exception))
        with self.assertRaises(setup.InvalidRequest):
            self.request_raising(http.client.InvalidURL("nonnumeric port: 'x'"))

    def test_invalid_request_masks_url_credentials(self):
        saved = setup.urllib.request.urlopen
        setup.urllib.request.urlopen = lambda *_a, **_k: (_ for _ in ()).throw(http.client.InvalidURL("nonnumeric port"))
        try:
            with self.assertRaises(setup.InvalidRequest) as caught:
                setup.Grafana("http://admin:s3cretPass@grafana:3000", TOKEN).request("GET", "/api/health")
        finally:
            setup.urllib.request.urlopen = saved
        self.assertIn("http://***@grafana:3000", str(caught.exception))
        self.assertNotIn("s3cretPass", str(caught.exception))

    def test_mask_userinfo(self):
        self.assertEqual(setup.mask_userinfo("https://user:pass@host/x@y"), "https://***@host/x@y")
        self.assertEqual(setup.mask_userinfo("http://host:3000/a@b"), "http://host:3000/a@b")
        self.assertEqual(setup.mask_userinfo("GRAFANA_URL is required"), "GRAFANA_URL is required")
        self.assertEqual(setup.mask_userinfo("unknown url type: 'ftp://u:p@h'; see http://a:b@c/d"), "unknown url type: 'ftp://u:p@h'; see http://***@c/d")

    def test_unreadable_answer_is_not_an_invalid_request(self):
        """A non-JSON or broken answer from Grafana is not a malformed URL: never InvalidRequest."""

        class Response:
            status = 200

            def __init__(self, read):
                self.read = read

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def raise_decode():
            raise json.JSONDecodeError("Expecting value", "x", 0)

        saved_open, saved_parse = setup.urllib.request.urlopen, setup.parse_json
        try:
            setup.urllib.request.urlopen = lambda *_a, **_k: Response(lambda: b"<html>proxy error</html>")
            self.assertEqual(setup.Grafana("http://grafana:3000", TOKEN).request("GET", "/api/health"), (200, "<html>proxy error</html>"))
            setup.parse_json = lambda _raw: raise_decode()
            with self.assertRaises(Exception) as caught:
                setup.Grafana("http://grafana:3000", TOKEN).request("GET", "/api/health")
            self.assertNotIsInstance(caught.exception, setup.InvalidRequest)
            setup.parse_json = saved_parse
            setup.urllib.request.urlopen = lambda *_a, **_k: Response(lambda: (_ for _ in ()).throw(http.client.IncompleteRead(b"")))
            with self.assertRaises(setup.SetupError) as caught:
                setup.Grafana("http://grafana:3000", TOKEN).request("GET", "/api/health")
            self.assertNotIsInstance(caught.exception, setup.InvalidRequest)
        finally:
            setup.urllib.request.urlopen, setup.parse_json = saved_open, saved_parse

    def test_token_redacted_from_an_invalid_request(self):
        def urlopen(*_args, **_kwargs):
            raise ValueError(f"unknown url type: {TOKEN}")

        env = {"GRAFANA_URL": "http://grafana:3000", "GRAFANA_SA_TOKEN": TOKEN, "LOKI_INTERNAL_URL": "http://loki:3100",
               "TEMPO_INTERNAL_URL": "http://tempo:3200", "PROMETHEUS_INTERNAL_URL": "http://prometheus:9090"}
        saved = setup.urllib.request.urlopen
        setup.urllib.request.urlopen = urlopen
        stderr = io.StringIO()
        try:
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(stderr):
                code = setup.main()
        finally:
            setup.urllib.request.urlopen = saved
        self.assertEqual(code, 1)
        self.assertIn("invalid request", stderr.getvalue())
        self.assertNotIn(TOKEN, stderr.getvalue())
        self.assertIn("***", stderr.getvalue())


class PythonTargetTest(unittest.TestCase):
    """The grafana-setup modules run in python:3.13-alpine; the tests run on the Python 3.12 of the machine.

    ruff (target-version py312) already refuses syntax newer than 3.12; this test refuses the
    modules that Python 3.13 no longer ships, and anything outside the standard library (the
    sibling modules of config/grafana-setup excepted).
    """

    REMOVED_BY_3_13 = {
        "aifc", "asynchat", "asyncore", "audioop", "cgi", "cgitb", "chunk", "crypt", "distutils", "imghdr", "imp", "lib2to3",
        "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "smtpd", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
    }

    def test_imports_exist_in_python_3_13(self):
        siblings = {path.stem for path in SETUP_DIR.glob("*.py")}
        self.assertEqual(siblings, {"alerting", "dashboards", "setup"})
        for path in sorted(SETUP_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
                self.assertEqual(imported & self.REMOVED_BY_3_13, set())
                self.assertEqual(imported - set(sys.stdlib_module_names) - siblings, set())


class GrafanaSetupHarnessTest(unittest.TestCase):
    """Against the real test Grafana started by `harness/edge.py up` (plan B: tests/test_exploitation.py)."""

    def setUp(self):
        require_harness(self)
        edge = json.loads((ROOT / ".harness" / "edge.json").read_text(encoding="utf-8"))
        self.env = dict(
            os.environ,
            GRAFANA_URL=edge["grafana_url"],
            GRAFANA_SA_TOKEN=edge["grafana_token"],
            LOKI_INTERNAL_URL="http://loki:3100",
            TEMPO_INTERNAL_URL="http://tempo:3200",
            PROMETHEUS_INTERNAL_URL="http://prometheus:9090",
            PROJECTS="demo,other-project",
            TELEGRAM_BOT_TOKEN="",
            TELEGRAM_CHAT_ID="",
            ALERT_EMAILS="ops@gc.test",
            GRAFANA_SETUP_FILES=";".join(f"{path}={digest}" for path, digest in render.grafana_setup_files(ROOT)),
            GRAFANA_SETUP_MIRROR_URL=f"file:{ROOT}",
        )
        self.api = setup.Grafana(edge["grafana_url"], edge["grafana_token"])

    def snapshot(self):
        datasources = {}
        for uid in (setup.LOKI_UID, setup.TEMPO_UID, setup.PROMETHEUS_UID):
            status, payload = self.api.request("GET", f"/api/datasources/uid/{uid}")
            self.assertEqual(status, 200)
            datasources[uid] = {k: payload[k] for k in ("uid", "name", "type", "url", "jsonData", "isDefault")}
        status, folders = self.api.request("GET", "/api/folders")
        self.assertEqual(status, 200)
        status, rules = self.api.request("GET", "/api/v1/provisioning/alert-rules")
        self.assertEqual(status, 200)
        return datasources, sorted(f["uid"] for f in folders), copy.deepcopy(sorted((r["uid"], r["data"]) for r in rules if r["uid"].startswith("gc-")))

    def test_idempotent_against_real_grafana(self):
        first = run([sys.executable, SETUP], env=self.env, timeout=120)
        self.assertEqual(first.returncode, 0, first.stdout)
        before = self.snapshot()
        second = run([sys.executable, SETUP], env=self.env, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertNotIn(": created", second.stdout)
        self.assertNotIn(": updated", second.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("gc-demo", before[1])
        self.assertNotIn(self.env["GRAFANA_SA_TOKEN"], first.stdout + second.stdout)


if __name__ == "__main__":
    unittest.main()
