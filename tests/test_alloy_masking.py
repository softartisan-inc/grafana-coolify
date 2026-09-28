import re
import unittest

from support import ROOT

CONFIG = ROOT / "config" / "alloy" / "config.alloy"
# The secret words, one (?i:...) group shared by every secret rule (OTTL and Faro, keys and text).
WORDS_RE = re.compile(r"\(\?i:authorization\|[^)]*\)")
# OTTL masking calls: function(target, ["value",] "pattern", ...) -> pattern.
OTTL_RE = re.compile(r'`(delete_matching_keys|replace_all_patterns|replace_pattern)\((?:[^,]+), (?:"value", )?"((?:[^"\\]|\\.)*)"')


def masking_literals(text):
    """{(function, rule kind): set of pattern literals} of the OTTL masking statements."""
    found = {}
    for function, pattern in OTTL_RE.findall(text):
        if function == "delete_matching_keys":
            kind = "ip-exempt keys" if "version" in pattern else "secret keys"
        elif "bearer" in pattern:
            kind = "bearer"
        elif "authorization" in pattern:
            kind = "free-text secrets"
        elif "@" in pattern:
            kind = "email"
        elif "\\\\d[ -]?" in pattern:
            kind = "card"
        else:
            kind = "ip"
        found.setdefault(kind, set()).add(pattern)
    return found


def drift(text):
    """Rule kinds whose literal differs between two contexts."""
    return sorted(kind for kind, patterns in masking_literals(text).items() if len(patterns) != 1)


class MaskingLiteralsTest(unittest.TestCase):
    """The masking rules are copied into every OTTL context: a copy must never drift."""

    def setUp(self):
        self.text = CONFIG.read_text(encoding="utf-8")

    def test_every_rule_kind_is_present_in_every_context(self):
        literals = masking_literals(self.text)
        self.assertEqual(sorted(literals), ["bearer", "card", "email", "free-text secrets", "ip", "ip-exempt keys", "secret keys"])
        # Secret keys are deleted in the 7 attribute maps and the map log body.
        self.assertEqual(len(re.findall(r"`delete_matching_keys\([^,]+, \"\^", self.text)), 8)

    def test_each_rule_has_a_single_literal(self):
        self.assertEqual(drift(self.text), [])

    def test_a_drifted_copy_is_detected(self):
        [secret_keys] = masking_literals(self.text)["secret keys"]
        drifted = self.text.replace(secret_keys, secret_keys.replace("token", "tokens"), 1)
        self.assertEqual(drift(drifted), ["secret keys"])

    def test_same_secret_words_on_otlp_and_faro(self):
        words = set(WORDS_RE.findall(self.text))
        self.assertEqual(len(words), 1, words)
        # OTTL: key deletion and free text; Faro: logfmt key drop and free text.
        self.assertEqual(len(WORDS_RE.findall(self.text)), 8 + 10 + 2)


if __name__ == "__main__":
    unittest.main()
