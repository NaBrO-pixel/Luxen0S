import os
import tempfile
import unittest

import helpers
from luxenos import config


assert helpers.CHROOT  # imported for its sys.path setup


class ConfigTest(unittest.TestCase):
    def test_parse_ignores_comments_and_bad_keys(self):
        v = config.parse_kv("# c\nA=1\n bad key=2\nlower=3\nB=\"two words\"\nC=x=y\n\n")
        self.assertEqual(v, {"A": "1", "B": "two words", "C": "x=y"})

    def test_update_preserves_other_lines_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.conf")
            with open(p, "w") as f:
                f.write("# keep me\nA=1\nB=2\n")
            config.update_kv(p, {"A": "9", "C": "3"}, remove=("B",))
            self.assertEqual(open(p).read(), "# keep me\nA=9\nC=3\n")
            self.assertEqual(os.listdir(d), ["x.conf"])

    def test_update_rejects_newlines(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                config.update_kv(os.path.join(d, "x"), {"A": "1\nEVIL=1"})
            with self.assertRaises(ValueError):
                config.update_kv(os.path.join(d, "x"), {"a b": "1"})

    def test_typed_getters(self):
        v = {"N": "50", "BAD": "x", "BIG": "500", "Y": "yes", "C": "left"}
        self.assertEqual(config.get_int(v, "N", 1, 0, 100), 50)
        self.assertEqual(config.get_int(v, "BAD", 1, 0, 100), 1)
        self.assertEqual(config.get_int(v, "BIG", 1, 0, 100), 1)
        self.assertTrue(config.get_bool(v, "Y", False))
        self.assertEqual(config.get_choice(v, "C", "bottom", ("bottom", "left")), "left")

    def test_live_detection(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("BOOT_IMAGE=/vmlinuz boot=live components quiet")
        try:
            self.assertTrue(config.is_live_session(f.name))
        finally:
            os.unlink(f.name)
        self.assertFalse(config.is_live_session("/nonexistent"))


if __name__ == "__main__":
    unittest.main()
