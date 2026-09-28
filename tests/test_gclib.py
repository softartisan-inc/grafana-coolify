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


if __name__ == "__main__":
    unittest.main()
