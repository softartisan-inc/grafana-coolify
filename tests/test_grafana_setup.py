import ast
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import ROOT, require_harness, run

SETUP = ROOT / "config" / "grafana-setup" / "setup.py"
TOKEN = "glsa_testTokenValue_0123456789abcdef"
sys.path.insert(0, str(SETUP.parent))
import setup  # noqa: E402


class FakeGrafana(BaseHTTPRequestHandler):
    """Minimal in-memory Grafana API: health, datasources by UID, folders by UID."""

    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
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
        state["requests"].append((method, self.path))
        if self.path == "/api/health":
            return self.reply(200, {"database": "ok", "version": state["version"]})
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply(401, {"message": "invalid API key"})
        if state.get("fail_with_token_echo"):
            return self.reply(500, {"message": "internal error for " + self.headers["Authorization"]})
        if self.path.startswith("/api/datasources/uid/"):
            uid = self.path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["datasources"][uid]) if uid in state["datasources"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["datasources"][uid] = self.body()
                return self.reply(200, {"message": "updated"})
        if self.path == "/api/datasources" and method == "POST":
            data = self.body()
            data.setdefault("isDefault", not state["datasources"])
            state["datasources"][data["uid"]] = data
            return self.reply(200, {"message": "created"})
        if self.path.startswith("/api/folders/"):
            uid = self.path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["folders"][uid]) if uid in state["folders"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["folders"][uid].update(title=self.body()["title"])
                return self.reply(200, state["folders"][uid])
        if self.path == "/api/folders" and method == "POST":
            data = self.body()
            state["folders"][data["uid"]] = data
            return self.reply(200, data)
        return self.reply(404, {"message": "unknown route"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")


class GrafanaSetupUnitTest(unittest.TestCase):
    def setUp(self):
        self.state = {"version": "13.2.2", "datasources": {}, "folders": {}, "requests": []}
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
            "PROJECTS": "in-immo, other-project,in-immo",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def setup_run(self, **overrides):
        return run([sys.executable, SETUP], env=dict(self.env, **overrides), timeout=60)

    def test_first_run_creates_everything(self):
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sorted(self.state["datasources"]), ["gc-loki", "gc-prometheus", "gc-tempo"])
        self.assertEqual(sorted(self.state["folders"]), ["gc-in-immo", "gc-other-project"])
        self.assertEqual(self.state["datasources"]["gc-loki"]["url"], "http://loki-abc:3100")

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.setup_run().returncode, 0)
        snapshot = json.dumps([self.state["datasources"], self.state["folders"]], sort_keys=True)
        self.state["requests"].clear()
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.dumps([self.state["datasources"], self.state["folders"]], sort_keys=True), snapshot)
        writes = [r for r in self.state["requests"] if r[0] in ("POST", "PUT")]
        self.assertEqual(writes, [])
        self.assertEqual(result.stdout.count(": unchanged"), 5)

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

    def test_invalid_project_name(self):
        result = self.setup_run(PROJECTS="ok,Not_Valid")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid project name", result.stdout)

    def test_missing_variable(self):
        result = self.setup_run(TEMPO_INTERNAL_URL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TEMPO_INTERNAL_URL is required", result.stdout)

    def test_parse_projects(self):
        self.assertEqual(setup.parse_projects(" a, b ,a,,"), ["a", "b"])
        self.assertEqual(setup.parse_projects(""), [])


class PythonTargetTest(unittest.TestCase):
    """setup.py runs in python:3.13-alpine; the tests run on the Python 3.12 of the machine.

    ruff (target-version py312) already refuses syntax newer than 3.12; this test refuses the
    modules that Python 3.13 no longer ships, and anything outside the standard library.
    """

    REMOVED_BY_3_13 = {
        "aifc", "asynchat", "asyncore", "audioop", "cgi", "cgitb", "chunk", "crypt", "distutils", "imghdr", "imp", "lib2to3",
        "mailcap", "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "smtpd", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
    }

    def test_imports_exist_in_python_3_13(self):
        tree = ast.parse(SETUP.read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertEqual(imported & self.REMOVED_BY_3_13, set())
        self.assertEqual(imported - set(sys.stdlib_module_names), set())


class GrafanaSetupHarnessTest(unittest.TestCase):
    """Against the real test Grafana started by `harness/stack.py up --with-edge`."""

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
        return datasources, sorted(f["uid"] for f in folders)

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
