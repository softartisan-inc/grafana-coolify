import sys
import unittest

from support import ROOT, promtool_check

sys.path.insert(0, str(ROOT / "config" / "grafana-setup"))
import alerting  # noqa: E402

BOT = "123456:fakeBotToken_abcdefghijklmnop"


class NotificationsTest(unittest.TestCase):
    """Variables of spec 11 for the notifications: validated before any call to Grafana."""

    def test_telegram_and_emails(self):
        notify = alerting.notifications({"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "-100123", "ALERT_EMAILS": " ops@example.com;dev@example.com , "})
        self.assertEqual(notify, {"telegram": {"chat_id": "-100123", "token": BOT}, "emails": ["ops@example.com", "dev@example.com"]})

    def test_nothing_configured(self):
        self.assertEqual(alerting.notifications({}), {"telegram": None, "emails": []})
        self.assertEqual(alerting.contact_points(alerting.notifications({})), [])

    def test_invalid_combinations(self):
        cases = {
            "go together": {"TELEGRAM_BOT_TOKEN": BOT},
            "ALERT_EMAILS is required": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "-100123"},
            "invalid address": {"ALERT_EMAILS": "ops@example.com,nobody"},
            "numeric chat id": {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "my group", "ALERT_EMAILS": "ops@example.com"},
        }
        for message, env in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(alerting.AlertingError, message):
                    alerting.notifications(env)

    def test_contact_points(self):
        env = {"TELEGRAM_BOT_TOKEN": BOT, "TELEGRAM_CHAT_ID": "@alerts_channel", "ALERT_EMAILS": "a@example.com,b@example.com"}
        points = alerting.contact_points(alerting.notifications(env))
        self.assertEqual([p["uid"] for p in points], ["gc-telegram", "gc-email"])
        self.assertEqual(points[0]["settings"], {"bottoken": BOT, "chatid": "@alerts_channel"})
        self.assertEqual(points[1]["settings"], {"addresses": "a@example.com;b@example.com", "singleEmail": True})


class PolicyTest(unittest.TestCase):
    TELEGRAM = {"telegram": {"chat_id": "-1", "token": BOT}, "emails": ["a@example.com"]}
    EMAIL_ONLY = {"telegram": None, "emails": ["a@example.com"]}
    MANUAL = {"receiver": "team-sms", "object_matchers": [["team", "=", "billing"]]}

    def test_default_policy(self):
        policy = alerting.desired_policy({"receiver": "empty", "group_by": ["grafana_folder", "alertname"]}, self.TELEGRAM)
        ours = {"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS}
        self.assertEqual(policy, {"receiver": "gc-email", "group_by": ["grafana_folder", "alertname"], "routes": [ours]})

    def test_manual_routes_are_kept_after_ours(self):
        unsorted = {"receiver": "gc-telegram", "object_matchers": [["severity", "=", "critical"], ["env", "=", "prod"]]}
        current = {"receiver": "gc-email", "routes": [self.MANUAL, unsorted]}
        routes = alerting.desired_policy(current, self.TELEGRAM)["routes"]
        self.assertEqual(routes, [{"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS}, self.MANUAL])

    def test_email_only_drops_our_route(self):
        current = {"receiver": "gc-email", "routes": [{"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS}]}
        self.assertEqual(alerting.desired_policy(current, self.EMAIL_ONLY), {"receiver": "gc-email", "group_by": alerting.DEFAULT_GROUP_BY})

    def test_settings_added_to_our_route_are_kept(self):
        tuned = {"receiver": "gc-telegram", "object_matchers": [["severity", "=", "critical"], ["env", "=", "prod"]], "continue": True, "group_wait": "10s"}
        routes = alerting.desired_policy({"receiver": "gc-email", "routes": [self.MANUAL, tuned]}, self.TELEGRAM)["routes"]
        expected = {"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS, "continue": True, "group_wait": "10s"}
        self.assertEqual(routes, [expected, self.MANUAL])

    def test_matcher_change_replaces_our_route(self):
        stale = {"receiver": "gc-telegram", "object_matchers": [["env", "=", "prod"], ["severity", "=", "warning"]], "continue": True}
        routes = alerting.desired_policy({"receiver": "gc-email", "routes": [stale, self.MANUAL]}, self.TELEGRAM)["routes"]
        self.assertEqual(routes, [{"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS, "continue": True}, self.MANUAL])

    def test_ensure_policy_rewrites_a_stale_route(self):
        stale = {"receiver": "gc-telegram", "object_matchers": [["env", "=", "prod"], ["severity", "=", "warning"]]}
        ours = {"receiver": "gc-telegram", "object_matchers": alerting.TELEGRAM_MATCHERS}
        current = {"receiver": "gc-email", "group_by": alerting.DEFAULT_GROUP_BY, "routes": [stale, ours]}
        calls = []

        class FakeApi:
            def expect(self, method, path, body=None, ok=(200,), headers=None):
                calls.append((method, path, body, headers))
                return current if method == "GET" else None

        self.assertEqual(alerting.ensure_policy(FakeApi(), self.TELEGRAM), "updated")
        method, path, body, headers = calls[-1]
        self.assertEqual((method, path, headers), ("PUT", f"{alerting.PROVISIONING}/policies", alerting.PROVENANCE))
        self.assertEqual([r for r in body["routes"] if r["receiver"] == "gc-telegram"], [ours])

    def test_matchers_sorted_like_grafana(self):
        self.assertEqual(alerting.TELEGRAM_MATCHERS, sorted(alerting.TELEGRAM_MATCHERS))


class RulesTest(unittest.TestCase):
    def rules(self, **env):
        return {r["uid"]: r for r in alerting.rules(alerting.thresholds(env), "gc-prometheus")}

    def test_thresholds_default_to_the_spec(self):
        expected = {"error_rate": 0.05, "p95_ms": 1500, "p95_min_calls": 100, "silence_min": 15, "disk_pct": 80, "cardinality": 200000, "host_env": "prod"}
        self.assertEqual(alerting.thresholds({}), expected)

    def test_six_rules_with_the_severities_of_the_spec(self):
        severities = {uid: rule["labels"]["severity"] for uid, rule in self.rules().items()}
        critical = {"gc-error-rate", "gc-silence", "gc-disk"}
        self.assertEqual({uid for uid, severity in severities.items() if severity == "critical"}, critical)
        self.assertEqual({uid for uid, severity in severities.items() if severity == "warning"}, {"gc-latency", "gc-cardinality", "gc-rejections"})

    def test_thresholds_reach_the_conditions(self):
        rules = self.rules(ALERT_ERROR_RATE="0.1", ALERT_P95_MS="800", ALERT_DISK_PCT="90", CARDINALITY_ALERT_THRESHOLD="5000")
        params = {uid: rule["data"][1]["model"]["conditions"][0]["evaluator"]["params"][0] for uid, rule in rules.items()}
        self.assertEqual(params, {"gc-error-rate": 0.1, "gc-latency": 800, "gc-silence": 0, "gc-disk": 90, "gc-cardinality": 5000, "gc-rejections": 0})

    def test_silence_window_follows_the_variable(self):
        self.assertIn("[15m]", self.rules()["gc-silence"]["data"][0]["model"]["expr"])
        long_rule = self.rules(ALERT_SILENCE_MIN="90")["gc-silence"]
        self.assertIn("[90m]", long_rule["data"][0]["model"]["expr"])
        self.assertIn("[360m]", long_rule["data"][0]["model"]["expr"])
        self.assertEqual(long_rule["data"][0]["relativeTimeRange"]["from"], 21600)

    def test_rules_keyed_by_service_carry_project_env_service(self):
        for uid in ("gc-error-rate", "gc-latency", "gc-silence"):
            with self.subTest(uid=uid):
                self.assertIn("project, env, service", self.rules()[uid]["data"][0]["model"]["expr"])

    def test_invalid_thresholds(self):
        for env, message in (
            ({"ALERT_ERROR_RATE": "5%"}, "not a valid float"),
            ({"ALERT_ERROR_RATE": "2"}, "out of range"),
            ({"ALERT_P95_MS": "1.5"}, "not a valid int"),
            ({"ALERT_P95_MIN_CALLS": "1.5"}, "ALERT_P95_MIN_CALLS='1.5' is not a valid int"),
            ({"ALERT_P95_MIN_CALLS": "beaucoup"}, "ALERT_P95_MIN_CALLS='beaucoup' is not a valid int"),
            ({"ALERT_P95_MIN_CALLS": "0"}, "ALERT_P95_MIN_CALLS='0' is out of range"),
            ({"ALERT_P95_MIN_CALLS": "-5"}, "ALERT_P95_MIN_CALLS='-5' is out of range"),
            ({"ALERT_P95_MIN_CALLS": "10000001"}, "ALERT_P95_MIN_CALLS='10000001' is out of range"),
            ({"ALERT_SILENCE_MIN": "0"}, "out of range"),
            ({"ALERT_DISK_PCT": "100"}, "out of range"),
            ({"CARDINALITY_ALERT_THRESHOLD": "-1"}, "out of range"),
        ):
            with self.subTest(env=env):
                with self.assertRaisesRegex(alerting.AlertingError, message):
                    alerting.thresholds(env)

    def test_latency_uses_5m_windows_and_a_volume_guard(self):
        """Anti-flapping (prod incident): no 10 min window, p95 kept only above ALERT_P95_MIN_CALLS requests."""
        expr = self.rules()["gc-latency"]["data"][0]["model"]["expr"]
        self.assertIn("rate(traces_spanmetrics_latency_bucket{" + alerting.REQUEST_KINDS + "}[5m])", expr)
        self.assertNotIn("[10m]", expr)
        guard = (
            " and on (project, env, service) (sum by (project, env, service) "
            "(increase(traces_spanmetrics_calls_total{" + alerting.REQUEST_KINDS + "}[5m])) >= 100)"
        )
        self.assertTrue(expr.endswith(guard), expr)
        self.assertIn(">= 2500)", self.rules(ALERT_P95_MIN_CALLS="2500")["gc-latency"]["data"][0]["model"]["expr"])

    def test_latency_query_range_covers_its_window(self):
        self.assertGreaterEqual(self.rules()["gc-latency"]["data"][0]["relativeTimeRange"]["from"], 300)

    def test_empty_min_calls_takes_the_default(self):
        """Coolify passes a variable emptied in its UI as an empty string, not the compose fallback."""
        for value in ("", "   "):
            with self.subTest(value=value):
                self.assertEqual(alerting.thresholds({"ALERT_P95_MIN_CALLS": value})["p95_min_calls"], 100)

    def test_latency_pending_and_keep_firing(self):
        latency = self.rules()["gc-latency"]
        self.assertEqual((latency["for"], latency["keep_firing_for"]), ("5m", "15m"))

    def test_other_rules_keep_their_behaviour(self):
        expected = {"gc-error-rate": "2m", "gc-silence": "0s", "gc-disk": "2m", "gc-cardinality": "2m", "gc-rejections": "0s"}
        rules = self.rules()
        self.assertEqual({uid: rules[uid]["for"] for uid in expected}, expected)
        self.assertEqual({uid: rules[uid]["keep_firing_for"] for uid in expected}, dict.fromkeys(expected, "0s"))

    def test_latency_summary_names_the_window_and_the_volume(self):
        summary = self.rules(ALERT_P95_MIN_CALLS="250")["gc-latency"]["annotations"]["summary"]
        self.assertIn("sur 5 min", summary)
        self.assertIn("250 requêtes", summary)
        self.assertNotIn("10 min", summary)

    def test_rejections_cover_every_counter_of_the_spec(self):
        expr = self.rules()["gc-rejections"]["data"][0]["model"]["expr"]
        for metric in alerting.REJECTION_METRICS:
            with self.subTest(metric=metric):
                self.assertIn(f"increase({metric}[15m])", expr)
                self.assertIn(f"{metric} unless {metric} offset 15m", expr)

    def test_promql_parses(self):
        for env in ({}, {"ALERT_SILENCE_MIN": "90"}):
            exprs = [(rule["uid"], rule["data"][0]["model"]["expr"]) for rule in alerting.rules(alerting.thresholds(env), "gc-prometheus")]
            with self.subTest(env=env):
                code, output = promtool_check(exprs)
                self.assertEqual(code, 0, output)

    def test_disk_carries_the_host_env(self):
        self.assertEqual(self.rules()["gc-disk"]["labels"], {"env": "prod", "severity": "critical"})
        self.assertEqual(self.rules(HOST_ENV="preprod")["gc-disk"]["labels"], {"env": "preprod", "severity": "critical"})
        with self.assertRaisesRegex(alerting.AlertingError, "HOST_ENV='staging' must be prod or preprod"):
            self.rules(HOST_ENV="staging")


class EnsureRuleTest(unittest.TestCase):
    """A rerun leaves every rule alone when Grafana returns what it stored (with its own extra fields)."""

    class FakeGrafana:
        """Stores rules like Grafana 13: adds id/updated, and keep_firing_for "0s" when it is omitted."""

        def __init__(self):
            self.rules = {}
            self.writes = []

        def request(self, method, path, body=None):
            uid = path.rsplit("/", 1)[1]
            return (200, self.rules[uid]) if uid in self.rules else (404, b"")

        def expect(self, method, path, body=None, ok=(200,), headers=None):
            self.writes.append((method, body["uid"]))
            self.rules[body["uid"]] = dict({"keep_firing_for": "0s"}, **body, id=1, updated="2026-10-09T00:00:00Z")

        def error(self, method, path, status, payload):
            return RuntimeError(f"{method} {path}: {status}")

    def desired(self, **env):
        return alerting.rules(alerting.thresholds(env), "gc-prometheus")

    def test_rerun_is_unchanged_for_all_six_rules(self):
        api = self.FakeGrafana()
        self.assertEqual([alerting.ensure_rule(api, item) for item in self.desired()], ["created"] * 6)
        self.assertEqual([alerting.ensure_rule(api, item) for item in self.desired()], ["unchanged"] * 6)
        self.assertEqual(len(api.writes), 6)

    def test_rule_stored_before_keep_firing_for_is_updated(self):
        """A gc-latency created by the previous version (no keep_firing_for, Grafana stored "0s") is rewritten."""
        api = self.FakeGrafana()
        for item in self.desired():
            alerting.ensure_rule(api, item)
        api.rules["gc-latency"]["keep_firing_for"] = "0s"
        results = {item["uid"]: alerting.ensure_rule(api, item) for item in self.desired()}
        self.assertEqual(results.pop("gc-latency"), "updated")
        self.assertEqual(set(results.values()), {"unchanged"})

    def test_min_calls_change_updates_only_the_latency_rule(self):
        api = self.FakeGrafana()
        for item in self.desired():
            alerting.ensure_rule(api, item)
        results = {item["uid"]: alerting.ensure_rule(api, item) for item in self.desired(ALERT_P95_MIN_CALLS="500")}
        self.assertEqual(results.pop("gc-latency"), "updated")
        self.assertEqual(set(results.values()), {"unchanged"})


if __name__ == "__main__":
    unittest.main()
