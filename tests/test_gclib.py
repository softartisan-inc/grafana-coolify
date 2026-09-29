import sys
import unittest

from support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import gclib  # noqa: E402


class YamlSectionValueTest(unittest.TestCase):
    def test_key_of_a_later_section_is_not_found(self):
        text = "retention:\n  other: 1\nlimits:\n  period: 720h\n"
        self.assertIsNone(gclib.yaml_section_value(text, "retention", "period"))

    def test_nested_key_inside_the_section_is_found(self):
        text = "server:\n  port: 1\nretention:\n  enabled: true\n  rules:\n    - period: '168h'\n      name: x\nlimits:\n  period: 720h\n"
        self.assertEqual(gclib.yaml_section_value(text, "retention", "period"), "168h")

    def test_missing_section_returns_none(self):
        self.assertIsNone(gclib.yaml_section_value("limits:\n  period: 720h\n", "retention", "period"))


class OtlpValueTest(unittest.TestCase):
    def test_list_attribute_is_an_array_value(self):
        self.assertEqual(
            gclib.attrs({"h": ["a", "b"]}),
            [{"key": "h", "value": {"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}}],
        )

    def test_dict_attribute_is_a_kvlist_value(self):
        self.assertEqual(
            gclib.attrs({"m": {"k": 1}}),
            [{"key": "m", "value": {"kvlistValue": {"values": [{"key": "k", "value": {"intValue": "1"}}]}}}],
        )

    def test_dict_log_body_is_a_kvlist_value(self):
        record = gclib.otlp_logs({}, {"k": "v"})["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        self.assertEqual(record["body"], {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}})

    def test_string_log_body_stays_a_string_value(self):
        record = gclib.otlp_logs({}, "line")["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        self.assertEqual(record["body"], {"stringValue": "line"})

    def test_scope_attributes_of_logs_and_traces(self):
        scope_logs = gclib.otlp_logs({}, "line", scope={"owner": "a"})["resourceLogs"][0]["scopeLogs"][0]
        scope_spans = gclib.otlp_traces({}, [], scope={"owner": "a"})["resourceSpans"][0]["scopeSpans"][0]
        for scope in (scope_logs["scope"], scope_spans["scope"]):
            self.assertEqual(scope["attributes"], [{"key": "owner", "value": {"stringValue": "a"}}])
            self.assertTrue(scope["name"])

    def test_no_scope_by_default(self):
        self.assertNotIn("scope", gclib.otlp_logs({}, "line")["resourceLogs"][0]["scopeLogs"][0])


if __name__ == "__main__":
    unittest.main()
