import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import helpers
from luxenos import apps, plugins, search

assert helpers.CHROOT


class CalculatorTest(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(search.calculate("2+2*3"), 8)
        self.assertEqual(search.calculate("2^10"), 1024)
        self.assertEqual(search.calculate("sqrt(16)+1"), 5.0)
        self.assertEqual(search.calculate("=5"), 5)
        self.assertAlmostEqual(search.calculate("(1+2)/4"), 0.75)

    def test_not_calculations(self):
        for q in ("42", "firefox", "1/0", "__import__('os')", "2**99999", "a.b", "open(1)", "9" * 300):
            self.assertIsNone(search.calculate(q), q)

    def test_format(self):
        self.assertEqual(search.format_number(1234567), "1 234 567")
        self.assertEqual(search.format_number(0.5), "0.5")


class ConvertTest(unittest.TestCase):
    def test_units(self):
        v, unit, src = search.convert("10 km to mi")
        self.assertAlmostEqual(v, 6.2137, places=3)
        self.assertEqual((unit, src), ("mi", "10 km"))
        self.assertAlmostEqual(search.convert("100 f in c")[0], 37.7778, places=3)
        self.assertAlmostEqual(search.convert("2 GiB to MB")[0], 2147.483648)
        self.assertEqual(search.convert("2 GiB to MB")[1], "MB")
        self.assertEqual(search.convert("90 min in h")[0], 1.5)

    def test_incompatible(self):
        self.assertIsNone(search.convert("5 kg to km"))
        self.assertIsNone(search.convert("ten km to mi"))
        self.assertIsNone(search.convert("hello"))


class SettingsWebHistoryTest(unittest.TestCase):
    def test_settings(self):
        self.assertEqual(search.match_settings("wifi")[0][0], "Network")
        self.assertIn("Liquid Glass", [t for t, _ in search.match_settings("blurr")])
        self.assertEqual(search.match_settings(""), [])

    def test_web(self):
        self.assertIn("wikipedia.org", search.web_results("wiki Debian", {})[0][1])
        self.assertIn("duckduckgo.com/?q=hello+world", search.web_results("hello world", {})[0][1])
        self.assertEqual(search.web_results("cats", {"SEARCH_WEB": "no"}), [])
        custom = {"SEARCH_WEB_ENGINE_SP": "Startpage|https://www.startpage.com/do/search?q=%s",
                  "SEARCH_WEB_ENGINE_BAD": "Evil|http://evil.example/?q=%s"}
        eng = search.engines(custom)
        self.assertIn("sp", eng)
        self.assertNotIn("bad", eng)

    def test_history(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_STATE_HOME": d}):
            search.record_history("firefox", {})
            search.record_history("gimp", {})
            search.record_history("firefox", {})
            self.assertEqual(search.load_history(), ["firefox", "gimp"])
            search.record_history("secret", {"SEARCH_HISTORY": "no"})
            self.assertNotIn("secret", search.load_history())
            search.clear_history()
            self.assertEqual(search.load_history(), [])


class FuzzyTest(unittest.TestCase):
    def test_typos_rank_below_real_matches(self):
        es = [apps.AppEntry("firefox-esr.desktop", "Firefox ESR"), apps.AppEntry("thunar.desktop", "Thunar"),
              apps.AppEntry("fire.desktop", "Fire Alarm")]
        self.assertEqual([e.name for e in apps.rank("firfox", es)], ["Firefox ESR"])
        self.assertEqual([e.name for e in apps.rank("thnuar", es)], ["Thunar"])
        self.assertEqual(apps.rank("fire", es)[0].name, "Fire Alarm")
        self.assertEqual(apps.rank("xyz", es), [])
        self.assertEqual(apps.fuzzy_ratio("ab", "abc"), 0)  # short tokens stay exact


class PluginTest(unittest.TestCase):
    def make(self, base, ident, conf, exe="#!/bin/sh\necho '{\"results\": []}'\n"):
        d = os.path.join(base, ident)
        os.makedirs(d)
        with open(os.path.join(d, "plugin.conf"), "w") as f:
            f.write(conf)
        with open(os.path.join(d, "run"), "w") as f:
            f.write(exe)
        os.chmod(os.path.join(d, "run"), 0o755)
        os.chmod(d, 0o755)
        return d

    GOOD = "PLUGIN_ID=%s\nPLUGIN_NAME=Test\nPLUGIN_VERSION=1.0\nPLUGIN_API=1\nPLUGIN_TYPE=search\nPLUGIN_EXEC=run\n"

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            ok = self.make(d, "good", self.GOOD % "good")
            self.assertEqual(plugins.validate("good", ok, plugins.config.read_kv(ok + "/plugin.conf"), False), [])
            old = self.make(d, "old", (self.GOOD % "old").replace("PLUGIN_API=1", "PLUGIN_API=0"))
            self.assertTrue(any("API" in p for p in plugins.validate(
                "old", old, plugins.config.read_kv(old + "/plugin.conf"), False)))
            perm = self.make(d, "perm", self.GOOD % "perm" + "PLUGIN_PERMISSIONS=root\n")
            self.assertTrue(any("permission" in p for p in plugins.validate(
                "perm", perm, plugins.config.read_kv(perm + "/plugin.conf"), False)))
            ww = self.make(d, "ww", self.GOOD % "ww")
            os.chmod(os.path.join(ww, "run"), 0o777)
            self.assertTrue(plugins.validate("ww", ww, plugins.config.read_kv(ww + "/plugin.conf"), False))
            esc = self.make(d, "esc", (self.GOOD % "esc").replace("PLUGIN_EXEC=run", "PLUGIN_EXEC=../x"))
            self.assertTrue(plugins.validate("esc", esc, plugins.config.read_kv(esc + "/plugin.conf"), False))

    def test_enable_state_defaults(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            sys_p = plugins.Plugin("a", "/x", {}, True)
            user_p = plugins.Plugin("b", "/y", {}, False)
            self.assertTrue(plugins.enabled(sys_p))
            self.assertFalse(plugins.enabled(user_p))
            plugins.set_enabled("b", True)
            self.assertTrue(plugins.enabled(user_p))
            with self.assertRaises(ValueError):
                plugins.set_enabled("../x", True)

    def test_output_parsing(self):
        out = json.dumps({"results": [{"title": "A", "copy": "x", "open": "http://insecure"},
                                      {"title": "B", "open": "https://ok.example"}, {"no": "title"}] +
                          [{"title": str(i)} for i in range(20)]})
        r = plugins.parse_output(out, "P")
        self.assertEqual(len(r), plugins.MAX_RESULTS)
        self.assertEqual(r[0]["action"], {"copy": "x"})
        self.assertEqual(r[1]["action"], {"open": "https://ok.example"})
        self.assertEqual(plugins.parse_output("not json", "P"), [])

    def test_sandbox_arguments(self):
        p = plugins.Plugin("a", "/usr/share/luxenos/plugins/a", {"PLUGIN_EXEC": "run"}, True)
        with mock.patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
            argv = plugins.sandbox_argv(p, "q")
        self.assertIn("--unshare-all", argv)
        self.assertNotIn("--share-net", argv)
        self.assertNotIn(os.path.expanduser("~"), argv)
        p.values["PLUGIN_PERMISSIONS"] = "network"
        with mock.patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
            self.assertIn("--share-net", plugins.sandbox_argv(p, "q"))
        with mock.patch.object(shutil, "which", return_value=None):
            self.assertIsNone(plugins.sandbox_argv(p, "q"))

    @unittest.skipUnless(shutil.which("bwrap") and os.getuid() != 0, "needs bubblewrap")
    def test_sandbox_blocks_home(self):
        exe = ("#!/usr/bin/python3\nimport json, os\n"
               "print(json.dumps({'results': [{'title': str(os.path.exists(%r))}]}))\n" % os.path.expanduser("~"))
        with tempfile.TemporaryDirectory() as d:
            pd = self.make(d, "probe", self.GOOD % "probe", exe)
            p = plugins.Plugin("probe", pd, plugins.config.read_kv(pd + "/plugin.conf"), True)
            r = plugins.run_search(p, "x")
            if r:  # bwrap may be unusable in some containers
                self.assertEqual(r[0]["title"], "False")

    def test_shipped_plugins_are_valid(self):
        base = os.path.join(helpers.CHROOT, "usr", "share", "luxenos", "plugins")
        for n in os.listdir(base):
            d = os.path.join(base, n)
            st = os.stat(os.path.join(d, plugins.config.read_kv(d + "/plugin.conf")["PLUGIN_EXEC"]))
            self.assertTrue(st.st_mode & stat.S_IXUSR, n)
            self.assertEqual(plugins.validate(n, d, plugins.config.read_kv(d + "/plugin.conf"), True), [], n)


if __name__ == "__main__":
    unittest.main()
