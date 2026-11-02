import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock

import helpers
from luxenos import notifications, overview, themeinstall, wallpaper, widgets

assert helpers.CHROOT


class WallpaperTest(unittest.TestCase):
    def test_commands_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "a.png")
            open(img, "wb").close()
            values = {"WALLPAPER": img, "WALLPAPER_MODE": "fit",
                      wallpaper.output_key("eDP-1"): "#112233", wallpaper.output_key("eDP-1") + "_NAME": "eDP-1",
                      "WALLPAPER_OUTPUT_BAD": "/etc/passwd", "WALLPAPER_OUTPUT_BAD_NAME": "x; exec rm"}
            cmds = wallpaper.static_commands(values)
            self.assertEqual(cmds[0], 'output * bg "%s" fit' % img)
            self.assertIn("output eDP-1 bg #112233 solid_color", cmds)
            self.assertEqual(len(cmds), 2)
            self.assertFalse(wallpaper.valid_value('/tmp/x".png'))
            self.assertTrue(wallpaper.dynamic({"WALLPAPER_WS_3": img}))
            self.assertFalse(wallpaper.dynamic({"WALLPAPER": img}))
            self.assertEqual(wallpaper.workspace_wallpapers({"WALLPAPER_WS_3": img, "WALLPAPER_WS_x": img}), {3: img})

    def test_default_when_unset(self):
        self.assertEqual(wallpaper.static_commands({}), ['output * bg "%s" fill' % wallpaper.DEFAULT])

    def test_update_and_fragment(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            os.environ.pop("SWAYSOCK", None)
            with self.assertRaises(ValueError):
                wallpaper.update({"WALLPAPER": "/nonexistent.png"})
            wallpaper.update({"WALLPAPER": "#000000"})
            with open(wallpaper.fragment_path()) as f:
                self.assertIn("output * bg #000000 solid_color", f.read())

    def test_slideshow_order(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("b.jpg", "a.png", ".hidden.png", "notes.txt"):
                open(os.path.join(d, n), "w").close()
            v = {"SLIDESHOW_DIR": d, "SLIDESHOW_SHUFFLE": "no"}
            first = wallpaper.next_slide(v)
            self.assertEqual(os.path.basename(first), "a.png")
            self.assertEqual(os.path.basename(wallpaper.next_slide(v, first)), "b.jpg")


class NotificationTest(unittest.TestCase):
    HIST = json.dumps({"type": "aa{sv}", "data": [[
        {"appname": {"data": "Firefox"}, "summary": {"data": "Hi"}, "id": {"data": 7}, "urgency": {"data": "NORMAL"}},
        {"appname": {"data": "Foot"}, "summary": {"data": "Done"}, "id": {"data": 9}},
        {"appname": {"data": "Firefox"}, "summary": {"data": "Again"}, "id": {"data": 11}}]]})

    def test_history_grouping(self):
        h = notifications.parse_history(self.HIST)
        self.assertEqual([n["id"] for n in h], [11, 9, 7])
        self.assertEqual([(a, len(i)) for a, i in notifications.group(h)], [("Firefox", 2), ("Foot", 1)])
        self.assertEqual(notifications.parse_history("garbage"), [])

    def test_rules(self):
        v = {"APP_FIREFOX_NAME": "Firefox", "APP_FIREFOX_MODE": "block",
             "APP_CHAT_NAME": "Chat", "APP_CHAT_MODE": "silent", "APP_CHAT_URGENCY": "low",
             "APP_EVIL_NAME": 'x"]\n[global]', "APP_EVIL_MODE": "block", "NOTIFY_HISTORY": "9999"}
        text = notifications.dropin_text(v)
        self.assertIn('appname = "Firefox"\n    skip_display = yes\n    history_ignore = yes', text)
        self.assertIn("set_urgency = low", text)
        self.assertNotIn("luxenos-app-evil", text)
        self.assertIn("history_length = 100", text)  # out of range -> default

    def test_sound_choice(self):
        with mock.patch.object(os.path, "isfile", return_value=True):
            self.assertEqual(notifications.sound_for({}, "Foot", "NORMAL"), notifications.DEFAULT_SOUND)
            self.assertEqual(notifications.sound_for({}, "Foot", "CRITICAL"), notifications.DEFAULT_CRITICAL)
            self.assertIsNone(notifications.sound_for({}, "Foot", "NORMAL", paused=True))
            self.assertIsNone(notifications.sound_for({"NOTIFY_SOUND": "no"}, "Foot", "NORMAL"))
            muted = {"APP_FOOT_NAME": "Foot", "APP_FOOT_SOUND": "no"}
            self.assertIsNone(notifications.sound_for(muted, "Foot", "NORMAL"))
        self.assertEqual(notifications.app_key("org.mozilla.Firefox!"), "ORG_MOZILLA_FIREFOX")


class OverviewTest(unittest.TestCase):
    def test_names(self):
        ws = {"name": "2: Mail", "num": 2, "windows": []}
        self.assertEqual(overview.display_name(ws), "Mail")
        self.assertEqual(overview.display_name({"name": "3", "num": 3}), "")
        self.assertEqual(overview.rename_cmd(ws, "Web  stuff"), 'rename workspace "2: Mail" to "2: Web stuff"')
        self.assertEqual(overview.rename_cmd(ws, ""), 'rename workspace "2: Mail" to "2"')
        self.assertEqual(overview.rename_cmd(ws, 'a"b'), 'rename workspace "2: Mail" to "2: a\\"b"')

    def test_close_moves_windows(self):
        wss = [{"name": "1", "num": 1, "windows": [{"id": 5}]}, {"name": "3", "num": 3, "windows": [{"id": 8}]}]
        cmds = overview.close_cmds(wss[1], wss)
        self.assertEqual(cmds, ['[con_id=8] move container to workspace "1"', 'workspace "1"'])
        alone = [{"name": "1", "num": 1, "windows": [{"id": 5}]}]
        self.assertEqual(overview.close_cmds(alone[0], alone)[-1], "workspace number 2")

    def test_preview_targets(self):
        tree = {"type": "root", "nodes": [{"type": "output", "name": "eDP-1", "nodes": [
            {"type": "workspace", "name": "1", "nodes": [
                {"type": "con", "id": 4, "app_id": "foot", "visible": True,
                 "rect": {"x": 0, "y": 30, "width": 800, "height": 600}},
                {"type": "con", "id": 5, "app_id": "x", "visible": False,
                 "rect": {"x": 0, "y": 0, "width": 800, "height": 600}}]}]}]}
        self.assertEqual(overview.preview_targets(tree), [(4, "0,30 800x600")])


class WidgetsTest(unittest.TestCase):
    def test_config(self):
        self.assertEqual(widgets.enabled({}), ["clock", "cpu", "ram"])
        self.assertEqual(widgets.enabled({"WIDGETS": "weather bogus music"}), ["weather", "music"])
        self.assertEqual(widgets.position({"WIDGET_CPU_POS": "100,200"}, "cpu", 0), (100, 200))
        self.assertEqual(widgets.position({"WIDGET_CPU_POS": "-1,x"}, "cpu", 1), (40, 230))

    def test_weather(self):
        self.assertIsNone(widgets.weather_url({}))
        self.assertIsNone(widgets.weather_url({"WEATHER_LAT": "100", "WEATHER_LON": "0"}))
        self.assertIsNone(widgets.weather_url({"WEATHER_LAT": "1", "WEATHER_LON": "2",
                                               "WEATHER_URL": "http://x/{lat}{lon}"}))
        url = widgets.weather_url({"WEATHER_LAT": "52.52", "WEATHER_LON": "13.41", "WEATHER_UNITS": "imperial"})
        self.assertTrue(url.startswith("https://api.open-meteo.com/"))
        self.assertIn("latitude=52.5200", url)
        self.assertIn("fahrenheit", url)
        w = widgets.parse_weather('{"current": {"temperature_2m": 21.4, "weather_code": 61, "wind_speed_10m": 9},'
                                  ' "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"}}')
        self.assertEqual((w["temp"], w["text"], w["icon"]), (21.4, "Light rain", "weather-showers-symbolic"))
        self.assertIsNone(widgets.parse_weather('{"current": {}}'))

    def test_mpris(self):
        self.assertEqual(widgets.parse_mpris_metadata({"xesam:title": "Song", "xesam:artist": ["A", "B"]}),
                         ("Song", "A, B"))


class ThemeInstallTest(unittest.TestCase):
    def archive(self, path, members):
        with tarfile.open(path, "w:gz") as t:
            for name, data, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "sym":
                    info.type, info.linkname = tarfile.SYMTYPE, data
                    t.addfile(info)
                else:
                    info.size = len(data)
                    info.mode = 0o755
                    t.addfile(info, io.BytesIO(data))

    def test_install_and_refusals(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d + "/share"}):
            good = d + "/good.tar.gz"
            self.archive(good, [("Pix/index.theme", b"[Icon Theme]\nName=Pix\nDirectories=48x48\n", "f"),
                                ("Pix/48x48/a.png", b"x", "f")])
            done = themeinstall.install(good)
            self.assertEqual(done[0][0], "icons")
            st = os.stat(os.path.join(done[0][1], "48x48", "a.png"))
            self.assertEqual(st.st_mode & 0o777, 0o644)  # executable bits dropped
            with self.assertRaises(themeinstall.InstallError):
                themeinstall.install(good)  # already installed
            themeinstall.install(good, replace=True)
            for bad in ([("../evil", b"x", "f")], [("/abs", b"x", "f")], [("Pix/link", "/etc/passwd", "sym")]):
                p = d + "/bad.tar.gz"
                self.archive(p, bad)
                with self.assertRaises(themeinstall.InstallError):
                    themeinstall.install(p)
            empty = d + "/empty.tar.gz"
            self.archive(empty, [("readme.txt", b"hi", "f")])
            with self.assertRaises(themeinstall.InstallError):
                themeinstall.install(empty)

    def test_glass_preset_validation(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d}):
            ok = d + "/ocean.theme"
            with open(ok, "w") as f:
                f.write("THEME_NAME=Ocean\nGLASS_ACCENT=#3366ff\n")
            self.assertTrue(themeinstall.install(ok)[0][1].endswith("ocean.theme"))
            bad = d + "/evil.theme"
            with open(bad, "w") as f:
                f.write("THEME_NAME=Evil\nLD_PRELOAD=/x\n")
            with self.assertRaises(themeinstall.InstallError):
                themeinstall.install(bad)


if __name__ == "__main__":
    unittest.main()
