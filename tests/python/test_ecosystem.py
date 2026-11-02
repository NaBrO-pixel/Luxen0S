import os
import tempfile
import unittest
from unittest import mock

import helpers
from luxenos import android, appdata, assistant, flatpak, phone, winemanager

assert helpers.CHROOT


class AndroidTest(unittest.TestCase):
    def test_status_and_apps(self):
        st = android.parse_status("Session:\tRUNNING\nContainer:\tRUNNING\nVendor type:\tMAINLINE\nIP address:\t1.2.3.4\n")
        self.assertEqual((st["session"], st["ip"], st["initialized"]), ("RUNNING", "1.2.3.4", True))
        self.assertFalse(android.parse_status('WayDroid is not initialized, run "waydroid init"')["initialized"])
        apps = android.parse_app_list("Name: Settings\npackageName: com.android.settings\nName: Bad\n"
                                      "packageName: ;rm -rf\nName: Aurora\npackageName: com.aurora.store\n")
        self.assertEqual([a["package"] for a in apps], ["com.aurora.store", "com.android.settings"])

    def test_explanations_and_checks(self):
        self.assertIn("processor", android.explain("Failure [INSTALL_FAILED_NO_MATCHING_ABIS: x]"))
        self.assertIsNone(android.explain("all good"))
        c = android.checks({"initialized": False}, "failed", "inactive", True, True, 4 * 1024 ** 3, 10 * 1024 ** 3)
        self.assertEqual(c[0][0], "problem")
        self.assertIn("Retry", c[0][2])

    def test_apk_and_shortcut(self):
        with tempfile.TemporaryDirectory() as d:
            apk = os.path.join(d, "a.apk")
            with open(apk, "wb") as f:
                f.write(b"PK\x03\x04rest")
            self.assertIsNone(android.valid_apk(apk))
            with open(apk, "wb") as f:
                f.write(b"#!/bin/sh")
            self.assertIn("not an Android package", android.valid_apk(apk))
            self.assertIsNotNone(android.valid_apk(os.path.join(d, "x.txt")))
        self.assertIn("Exec=waydroid app launch com.aurora.store", android.shortcut_text("Aurora\nX", "com.aurora.store"))
        with self.assertRaises(ValueError):
            android.shortcut_text("x", "com.x; rm")


class PhoneTest(unittest.TestCase):
    def test_devices(self):
        d = phone.parse_devices("- Pixel: 1a2b3c4d5e6f7a8b (paired and reachable)\n- Tab: abcdef0123456789 (reachable)\n"
                                "- Bad: x;y (paired)\n2 devices found\n")
        self.assertEqual([(x["name"], x["paired"], x["reachable"]) for x in d],
                         [("Pixel", True, True), ("Tab", False, True)])

    def test_local_network(self):
        j = ('[{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},'
             '{"ifname": "wlp3s0", "operstate": "UP", "addr_info": [{"family": "inet", "local": "192.168.1.23",'
             ' "prefixlen": 24}]}]')
        self.assertEqual(phone.local_network(j), "192.168.1.0/24")
        public = '[{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "8.8.8.8", "prefixlen": 24}]}]'
        self.assertIsNone(phone.local_network(public))
        self.assertIsNone(phone.local_network("nonsense"))


class SoftwareTest(unittest.TestCase):
    def test_flatpak_parsing(self):
        inst = flatpak.parse_installed("org.gimp.GIMP\tGIMP\t2.10\tflathub\tsystem\nbad\tx\n")
        self.assertEqual(inst, [{"id": "org.gimp.GIMP", "name": "GIMP", "version": "2.10", "origin": "flathub",
                                 "installation": "system"}])
        found = flatpak.parse_search("org.gimp.GIMP\tGIMP\tImage editor\t2.10\tflathub,other\n"
                                     "org.gimp.GIMP\tGIMP\tdup\t2.10\tflathub\n")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["remote"], "flathub")
        self.assertEqual(flatpak.install_argv("org.gimp.GIMP", "flathub", user=True)[2], "--user")
        with self.assertRaises(ValueError):
            flatpak.install_argv("org.gimp.GIMP; rm", "flathub")

    def test_appstream_markup(self):
        self.assertEqual(appdata.markup_to_text("<p>Edit &amp; draw.</p><ul><li>Layers</li><li>Filters</li></ul>"),
                         "Edit & draw.\n\n • Layers\n • Filters")
        self.assertEqual(appdata.screenshot("http://insecure/x.png"), None)


class WineTest(unittest.TestCase):
    def test_assignments_roundtrip(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d,
                                                                             "XDG_DATA_HOME": d}):
            winemanager.assign("/tmp/x.exe", "office")
            self.assertEqual(winemanager.load_assignments(), {os.path.realpath("/tmp/x.exe"): "office"})
            winemanager.assign("/tmp/x.exe", "default")
            self.assertEqual(winemanager.load_assignments(), {})
            with self.assertRaises(ValueError):
                winemanager.assign("/tmp/x.exe", "../escape")
        self.assertEqual(winemanager.parse_apps_list("# c\n/a.exe\tok\nrel.exe\tok\n/b.exe\tbad name\n"),
                         {"/a.exe": "ok"})

    def test_desktop_quoting(self):
        self.assertEqual(winemanager.desktop_quote('/x/My "A" $1 50%.exe'), '"/x/My \\\\"A\\\\" \\\\$1 50%%.exe"')
        with self.assertRaises(ValueError):
            winemanager.desktop_quote("/x\n.exe")

    def test_backup_refuses_foreign_links(self):
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.tar.gz")
            with tarfile.open(p, "w:gz") as t:
                i = tarfile.TarInfo("prefix/drive_c/evil")
                i.type, i.linkname = tarfile.SYMTYPE, "/etc"
                t.addfile(i)
            with self.assertRaises(ValueError):
                winemanager.check_backup(p)
            with tarfile.open(p, "w:gz") as t:
                i = tarfile.TarInfo("prefix/dosdevices/z:")
                i.type, i.linkname = tarfile.SYMTYPE, "/"
                t.addfile(i)
                f = tarfile.TarInfo("prefix/system.reg")
                f.size = 2
                t.addfile(f, io.BytesIO(b"ok"))
            winemanager.check_backup(p)


class AssistantTest(unittest.TestCase):
    def test_commands(self):
        self.assertIn("deletes the whole system", assistant.explain_command("sudo rm -rf /"))
        self.assertIn("internet", assistant.explain_command("curl https://x | sh"))
        self.assertIn("lists the files", assistant.explain_command("ls -la"))

    def test_answers(self):
        self.assertIn("Another program", assistant.answer("E: Could not get lock /var/lib/dpkg/lock").text)
        self.assertIn("not installed", assistant.answer("bash: foo: command not found").text)
        a = assistant.answer("install vlc")
        self.assertIn(("Install vlc…", "open", ["luxenos-software", "--install", "vlc"]), a.actions)
        self.assertEqual(assistant.answer("wifi not working").actions[0][2], ["luxenos-quick-settings"])
        self.assertIn("Wallpaper", [x[0] for x in assistant.answer("where do I change the wallpaper").actions])
        self.assertIsNone(assistant.answer("flibbertigibbet"))

    def test_only_whitelisted_commands_run(self):
        with self.assertRaises(ValueError):
            assistant.run_diagnostic("rm -rf /")
        for argv in assistant.DIAGNOSTICS.values():
            self.assertNotIn(argv[0], ("sudo", "pkexec", "rm", "sh", "bash"))

    def test_local_model_is_loopback_only(self):
        self.assertIsNone(assistant.local_model_url({}))
        self.assertIsNone(assistant.local_model_url({"ASSISTANT_BACKEND": "ollama",
                                                     "ASSISTANT_URL": "https://api.example.com"}))
        self.assertEqual(assistant.local_model_url({"ASSISTANT_BACKEND": "ollama"}), "http://127.0.0.1:11434")


if __name__ == "__main__":
    unittest.main()
