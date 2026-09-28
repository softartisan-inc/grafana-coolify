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


# Statement blocks of the shared "default" transform: (signal, context, [statements]).
BLOCK_RE = re.compile(r'(trace|log|metric)_statements \{\s*context\s*=\s*"(\w+)"\s*statements = \[\n(.*?)\n\s*\]', re.S)
STATEMENT_RE = re.compile(r"^\s*`(\w+)\(([^,)]+)", re.M)
MASKING = ("delete_matching_keys", "replace_all_patterns")
# Every map the rules mask, per (signal, context): its attributes, and the map log body.
MASKED_MAPS = {
    ("trace", "resource"): {"resource.attributes"},
    ("trace", "scope"): {"scope.attributes"},
    ("trace", "span"): {"span.attributes"},
    ("trace", "spanevent"): {"spanevent.attributes"},
    ("log", "resource"): {"resource.attributes"},
    ("log", "scope"): {"scope.attributes"},
    ("log", "log"): {"log.attributes", "log.body"},
    ("metric", "resource"): {"resource.attributes"},
    ("metric", "scope"): {"scope.attributes"},
    ("metric", "datapoint"): {"datapoint.attributes"},
}


def default_blocks(text):
    """{(signal, context): [(function, first argument, statement)]} of the "default" transform."""
    start = text.index('otelcol.processor.transform "default"')
    body = text[start : text.index("\n}\n", start)]
    blocks = {}
    for signal, context, statements in BLOCK_RE.findall(body):
        found = [(m.group(1), m.group(2).strip(), statements[m.start() :].split("\n", 1)[0]) for m in STATEMENT_RE.finditer(statements)]
        blocks.setdefault((signal, context), []).extend(found)
    return blocks


def copy_segments(statements):
    """{map: statements from set(cache["flat"], map) to its conditional write-back}."""
    segments = {}
    for i, (function, first, statement) in enumerate(statements):
        if function == "set" and first == 'cache["flat"]':
            target = statement.split(", ", 1)[1].split(")", 1)[0]
            end = next(j for j in range(i, len(statements)) if statements[j][0] == "set" and statements[j][1] == target)
            segments[target] = statements[i : end + 1]
    return segments


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
        # Secret keys are deleted in the 7 attribute maps, the 3 scope attribute maps and the map log body.
        self.assertEqual(len(re.findall(r"`delete_matching_keys\([^,]+, \"\^", self.text)), 11)

    def test_each_rule_has_a_single_literal(self):
        self.assertEqual(drift(self.text), [])

    def test_a_drifted_copy_is_detected(self):
        [secret_keys] = masking_literals(self.text)["secret keys"]
        drifted = self.text.replace(secret_keys, secret_keys.replace("token", "tokens"), 1)
        self.assertEqual(drift(drifted), ["secret keys"])

    def test_same_secret_words_on_otlp_and_faro(self):
        words = set(WORDS_RE.findall(self.text))
        self.assertEqual(len(words), 1, words)
        # OTTL: key deletion (11 maps) and free text (13); Faro: logfmt key drop and free text.
        self.assertEqual(len(WORDS_RE.findall(self.text)), 11 + 13 + 2)

    def test_every_masked_map_is_known(self):
        masked = {}
        for key, statements in default_blocks(self.text).items():
            targets = {target for function, target, _ in statements if function in MASKING and not target.startswith("cache")}
            targets |= set(copy_segments(statements))
            if targets:
                masked[key] = targets
        self.assertEqual(masked, MASKED_MAPS)

    def test_attribute_maps_are_masked_on_a_flattened_copy(self):
        """Flattened copy, rules on the copy, written back only when a rule changed it (clean data keeps its shape)."""
        blocks = default_blocks(self.text)
        for key, targets in MASKED_MAPS.items():
            for target in targets - {"log.body"}:
                with self.subTest(block=key, target=target):
                    statements = blocks[key]
                    segment = copy_segments(statements)[target]
                    prologue = [f'`set(cache["flat"], {target})`,', '`flatten(cache["flat"])`,', '`set(cache["orig"], cache["flat"])`,']
                    self.assertEqual([s.strip() for _, _, s in segment[:3]], prologue)
                    self.assertEqual(segment[-1][2].strip(), f'`set({target}, cache["flat"]) where cache["flat"] != cache["orig"]`,')
                    kinds = masking_literals("\n".join(s for _, _, s in segment))
                    self.assertEqual(sorted(set(kinds) - {"card"}), ["bearer", "email", "free-text secrets", "ip", "ip-exempt keys", "secret keys"])
                    for function, first, statement in segment:
                        if function in MASKING or function == "replace_pattern":
                            self.assertTrue(first.startswith(('cache["flat"]', 'cache["ip"]')), statement)
                    direct = [s for f, t, s in statements if f in (*MASKING, "replace_pattern", "merge_maps") and t.split("[")[0] == target]
                    self.assertEqual(direct, [])

    def test_map_log_body_is_flattened_first(self):
        """The map body is flattened in place: nested maps and arrays reach the rules as dotted leaves."""
        statements = default_blocks(self.text)[("log", "log")]
        first_mask = next(i for i, (f, t, _) in enumerate(statements) if f in MASKING and t == "log.body")
        # Plain flatten: resolveConflicts would name array elements k, k.0, k.1 instead of k.0, k.1, k.2.
        flattens = [i for i, (f, t, s) in enumerate(statements) if f == "flatten" and "flatten(log.body) where IsMap(log.body)" in s]
        self.assertTrue(flattens and flattens[0] < first_mask, statements[:first_mask])

    def test_error_message_is_free_text(self):
        """error.message (OTel semantic conventions) gets the card rule like exception.message."""
        blocks = default_blocks(self.text)
        expected = {
            ("trace", "span"): ['cache["flat"]["error.message"]'],
            ("trace", "spanevent"): ['cache["flat"]["error.message"]'],
            ("log", "log"): ['log.body["error.message"]', 'cache["flat"]["error.message"]'],
        }
        for key, targets in expected.items():
            for target in targets:
                with self.subTest(block=key, target=target):
                    rules = [s for f, t, s in blocks[key] if f == "replace_pattern" and t == target]
                    self.assertEqual([kind for s in rules for kind in masking_literals(s)], ["card"])

    def test_scope_attributes_get_every_attribute_rule(self):
        blocks = default_blocks(self.text)
        for signal in ("trace", "log", "metric"):
            with self.subTest(signal=signal):
                kinds = masking_literals("\n".join(s for _, _, s in blocks[(signal, "scope")]))
                self.assertEqual(sorted(kinds), ["bearer", "email", "free-text secrets", "ip", "ip-exempt keys", "secret keys"])

    def test_list_log_body_becomes_text_before_the_string_rules(self):
        statements = default_blocks(self.text)[("log", "log")]
        first_string_rule = next(i for i, (f, t, s) in enumerate(statements) if f == "replace_pattern" and t == "log.body")
        convert = [i for i, (f, t, s) in enumerate(statements) if f == "set" and t == "log.body" and "String(log.body)" in s and "IsList(log.body)" in s]
        self.assertTrue(convert and convert[0] < first_string_rule, statements[:first_string_rule])


if __name__ == "__main__":
    unittest.main()
