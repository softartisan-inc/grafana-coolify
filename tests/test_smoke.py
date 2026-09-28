import hashlib
import sys
import unittest

from support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import smoke  # noqa: E402


class IpHashTest(unittest.TestCase):
    def ctx(self, settings):
        ctx = smoke.Ctx.__new__(smoke.Ctx)
        ctx.s = settings
        return ctx

    def test_missing_salt_is_explained(self):
        for settings in ({}, {"IP_HASH_SALT": ""}):
            with self.subTest(settings=settings), self.assertRaisesRegex(AssertionError, "IP_HASH_SALT is not set"):
                self.ctx(settings).ip_hash("192.0.2.1")

    def test_digest_is_sha256_of_salt_then_ip(self):
        self.assertEqual(self.ctx({"IP_HASH_SALT": "s"}).ip_hash("1.2.3.4"), hashlib.sha256(b"s1.2.3.4").hexdigest())

if __name__ == "__main__":
    unittest.main()
