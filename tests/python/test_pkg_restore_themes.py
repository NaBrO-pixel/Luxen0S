import os
import tempfile
import unittest
from unittest import mock

import helpers
from luxenos import pkg, restore, themes, welcome


class PkgTest(unittest.TestCase):
    def test_names(self):
        for ok in ("vlc", "g++", "libstdc++6", "wine32:i386", "python3.13"):
            self.assertTrue(pkg.valid_name(ok), ok)
        for bad in ("-o", "Vlc", "a", "x;rm", "foo-", "a b", "../x", "x:"):
            self.assertFalse(pkg.valid_name(bad), bad)

    def test_origins(self):
        deb = {"origin": "Debian", "label": "Debian-Security", "site": "security.debian.org",
               "archive": "stable-security", "trusted": True}
        third = {"origin": "waydroid", "label": "waydroid", "site": "repo.waydro.id", "archive": "trixie",
                 "trusted": True}
        now = {"origin": "", "label": "", "site": "", "archive": "now", "trusted": False}
        self.assertEqual(pkg.classify_origins([deb])[0], "debian")
        self.assertEqual(pkg.classify_origins([third, now])[0], "thirdparty")
        self.assertEqual(pkg.classify_origins([now])[0], "local")
        self.assertEqual(pkg.classify_origins([dict(third, trusted=False)])[0], "unverified")
        self.assertEqual(pkg.classify_origins([dict(deb, trusted=False)])[0], "unverified")

    def test_categories_and_libraries(self):
        self.assertEqual(pkg.category_of("contrib/games"), "games")
        self.assertEqual(pkg.category_of("weird"), "system")
        self.assertTrue(pkg.is_library_like("libfoo1"))
        self.assertTrue(pkg.is_library_like("foo-dev"))
        self.assertTrue(pkg.is_library_like("anything", "libs"))
        self.assertFalse(pkg.is_library_like("vlc", "video"))

    def test_status_lines(self):
        self.assertEqual(pkg.parse_status_line("pmstatus:vlc:42.5:Installing vlc"), ("install", 42.5, "Installing vlc"))
        self.assertEqual(pkg.parse_status_line("dlstatus:1:10:Downloading x"), ("download", 10.0, "Downloading x"))
        self.assertEqual(pkg.parse_status_line("@@STATUS Working"), ("status", None, "Working"))
        self.assertEqual(pkg.parse_status_line("@@LOG E: bad")[0], "log")

    def test_simulation_parse(self):
        out = ("Inst libfoo (1.0 Debian:13 [amd64])\nInst vlc [3.0.1] (3.0.2 Debian:13 [amd64])\n"
               "Remv oldpkg [1.0]\nConf vlc\nE: Unable to locate package nope\n")
        r = pkg.parse_simulation(out)
        self.assertEqual(r["install"], ["libfoo"])
        self.assertEqual(r["upgrade"], ["vlc"])
        self.assertEqual(r["remove"], ["oldpkg"])
        self.assertEqual(r["errors"], ["Unable to locate package nope"])

    def test_simulate_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            pkg.simulate("install", ["-o=Dpkg::Options::=evil"])
        with self.assertRaises(ValueError):
            pkg.simulate("purge", ["vlc"])

    def test_diagnose(self):
        self.assertTrue(pkg.diagnose(["E: dpkg was interrupted, you must manually run 'dpkg --configure -a'"])[1])
        self.assertIn("network", pkg.diagnose(["W: Failed to fetch http://x"])[0])
        self.assertIn("signature", pkg.diagnose(["NO_PUBKEY 1234"])[0])
        self.assertIn("Another program", pkg.diagnose(["E: Could not get lock /var/lib/dpkg/lock"])[0])

    def test_search(self):
        mk = lambda n, s, sec="utils": pkg.PackageInfo(name=n, summary=s, section=sec, category=pkg.category_of(sec),
                                                        library=pkg.is_library_like(n, sec))
        cat = [mk("vlc", "media player", "video"), mk("libvlc5", "vlc library", "libs"), mk("mpv", "video player")]
        self.assertEqual([p.name for p in pkg.search(cat, "vlc")], ["vlc"])
        self.assertEqual([p.name for p in pkg.search(cat, "vlc", include_libraries=True)], ["vlc", "libvlc5"])
        self.assertEqual([p.name for p in pkg.search(cat, "player")], ["vlc", "mpv"])
        self.assertEqual([p.name for p in pkg.search(cat, "", category="media")], ["vlc"])

    def test_protected_lists_match_helper(self):
        helper = open(os.path.join(helpers.CHROOT, "usr/libexec/luxenos/luxenos-pkg-root")).read()
        line = [l for l in helper.splitlines() if l.startswith("PROTECTED=")][0]
        self.assertEqual(sorted(line.split("=", 1)[1].strip('"').split()), sorted(pkg.PROTECTED))


class RestoreTest(unittest.TestCase):
    def test_parse_points(self):
        p = restore.parse_point("luxenos-backup-20260101-120000",
                                "FORMAT=2\nCREATED=1767268800\nDESCRIPTION=Before update\nTRIGGER=update\n")
        self.assertEqual((p.description, p.trigger, p.legacy), ("Before update", "update", False))
        self.assertIsNone(restore.parse_point("luxenos-backup-20260101-120000", "FORMAT=9\n"))
        legacy = restore.parse_point("luxenos-backup-20250101-010203", None, "Backup Date: x\n")
        self.assertTrue(legacy.legacy)
        self.assertIsNotNone(legacy.created)

    def test_list_ignores_foreign_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("luxenos-backup-20260101-120000", "luxenos-backup-20260102-120000", "other"):
                os.mkdir(os.path.join(d, name))
                with open(os.path.join(d, name, "info.conf"), "w") as f:
                    f.write("FORMAT=2\nDESCRIPTION=%s\n" % name)
            os.symlink(os.path.join(d, "other"), os.path.join(d, "luxenos-backup-20260103-120000"))
            ids = [p.id for p in restore.list_points(d)]
            self.assertEqual(ids, ["luxenos-backup-20260102-120000", "luxenos-backup-20260101-120000"])

    def test_snapshot_note_is_honest(self):
        self.assertIn("no snapshots", restore.snapshot_note("ext4"))
        self.assertIn("does not manage", restore.snapshot_note("btrfs"))
        self.assertEqual(restore.parse_item("@@ITEM config changed /etc/x"), ("config", "changed /etc/x"))


class ThemesTest(unittest.TestCase):
    def test_shipped_packs_are_valid(self):
        d = os.path.join(helpers.CHROOT, "usr/share/luxenos/themes")
        names = sorted(n for n in os.listdir(d) if n.endswith(".theme"))
        self.assertGreaterEqual(len(names), 3)
        with mock.patch("os.path.isfile", return_value=True):
            for n in names:
                values, errors = themes.load(os.path.join(d, n))
                self.assertEqual(errors, [], n)
                wp = values.get("WALLPAPER")
                if wp:
                    self.assertTrue(os.path.exists(os.path.join(helpers.CHROOT, wp.lstrip("/"))), wp)

    def test_validation_rejects_injection(self):
        bad = {"THEME_NAME": "x", "WALLPAPER": "/usr/share/backgrounds/a.png; exec rm -rf ~"}
        self.assertTrue(themes.validate(bad))
        self.assertTrue(themes.validate({"THEME_NAME": "x", "GLASS_ACCENT": "red"}))
        self.assertTrue(themes.validate({"THEME_NAME": "x", "EVIL": "1"}))
        self.assertTrue(themes.validate({"THEME_NAME": "x", "WALLPAPER": "/etc/shadow.png"}))
        self.assertTrue(themes.validate({"GLASS_THEME": "dark"}))
        self.assertEqual(themes.wallpaper_command({"WALLPAPER_COLOR": "#112233"}), "output * bg #112233 solid_color")


class WelcomeTest(unittest.TestCase):
    def test_layouts_and_fragment(self):
        text = "! model\n  pc105  Generic\n\n! layout\n  us   English (US)\n  de   German\n\n! variant\n  x us: y\n"
        self.assertEqual(welcome.parse_layouts(text), [("us", "English (US)"), ("de", "German")])
        self.assertEqual(welcome.keyboard_fragment("de"), "input type:keyboard xkb_layout de")
        with self.assertRaises(ValueError):
            welcome.keyboard_fragment("de; exec x")

    def test_autostart_rules(self):
        with tempfile.TemporaryDirectory() as d:
            live = os.path.join(d, "cmdline-live")
            inst = os.path.join(d, "cmdline")
            open(live, "w").write("boot=live quiet")
            open(inst, "w").write("root=UUID=x quiet")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
                self.assertFalse(welcome.should_autostart(live))
                self.assertTrue(welcome.should_autostart(inst))
                welcome.mark_done()
                self.assertFalse(welcome.should_autostart(inst))


if __name__ == "__main__":
    unittest.main()
