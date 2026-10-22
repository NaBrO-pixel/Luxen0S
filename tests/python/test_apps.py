import os
import tempfile
import unittest

import helpers
from luxenos import apps, filesearch, launcher
from luxenos.apps import AppEntry


assert helpers.CHROOT  # imported for its sys.path setup


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            AppEntry("firefox-esr.desktop", "Firefox ESR", "Web Browser", ("internet", "www"), "Browse the web"),
            AppEntry("foot.desktop", "Foot", "Terminal", ("shell", "console"), "Wayland terminal"),
            AppEntry("thunar.desktop", "Files", "File Manager", ("folder",), "Browse the file system"),
            AppEntry("gimp.desktop", "GNU Image Manipulation Program", "Image Editor", (), "Edit photos"),
            AppEntry("x.desktop", "Café Éditeur", "", (), ""),
        ]

    def test_name_beats_description(self):
        self.assertEqual([e.app_id for e in apps.rank("browse", self.entries)][:1], ["firefox-esr.desktop"])
        self.assertEqual(apps.rank("files", self.entries)[0].app_id, "thunar.desktop")

    def test_keywords_generic_and_multiword(self):
        self.assertEqual(apps.rank("terminal", self.entries)[0].app_id, "foot.desktop")
        self.assertEqual(apps.rank("image edit", self.entries)[0].app_id, "gimp.desktop")
        self.assertEqual(apps.rank("zzz", self.entries), [])

    def test_accents_and_usage(self):
        self.assertEqual(apps.rank("cafe", self.entries)[0].app_id, "x.desktop")
        a = AppEntry("a.desktop", "Text A")
        b = AppEntry("b.desktop", "Text B")
        self.assertEqual(apps.rank("text", [a, b], usage={"b.desktop": 5})[0], b)

    def test_exec_basename(self):
        self.assertEqual(apps.exec_basename("env FOO=1 /usr/bin/prog %U"), "prog")
        self.assertEqual(apps.exec_basename("sh /usr/local/bin/luxenos-x"), "luxenos-x")
        self.assertEqual(apps.exec_basename(""), "")

    def test_actions(self):
        acts = [("Shut Down", "i", ["true"], True, "power off")]
        self.assertEqual(len(launcher.match_actions("power", acts)), 1)
        self.assertTrue(launcher.match_actions("power", acts)[0].confirm)
        self.assertEqual(launcher.match_actions("nothing", acts), [])


class FileSearchTest(unittest.TestCase):
    def test_hidden_and_outside_home_are_skipped(self):
        home = "/home/u"
        paths = ["/home/u/Documents/report.odt", "/home/u/.cache/report.tmp", "/etc/report",
                 "/home/u/Old/annual-report.pdf"]
        self.assertEqual(filesearch.filter_matches(paths, "report", home, 10),
                         ["/home/u/Documents/report.odt", "/home/u/Old/annual-report.pdf"])

    def test_walk_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(30):
                open(os.path.join(d, "f%d" % i), "w").close()
            os.mkdir(os.path.join(d, ".hidden"))
            found = filesearch.walk_limited([d], max_entries=10)
            self.assertEqual(len(found), 10)
            self.assertFalse(any(".hidden" in p for p in filesearch.walk_limited([d])))


if __name__ == "__main__":
    unittest.main()
