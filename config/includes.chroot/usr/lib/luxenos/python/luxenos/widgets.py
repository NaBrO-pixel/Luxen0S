"""Desktop widgets: small glass cards on the desktop, behind your windows.

Widgets: clock and calendar, weather, CPU, memory, battery, network speed,
storage, music controls (any MPRIS player, including phones connected through
KDE Connect). Each enabled widget is its own wlr-layer-shell surface on the
background layer of the focused monitor; drag a widget to move it (the
position is saved). Without layer-shell they are ordinary small windows.

Settings: ~/.config/luxenos/widgets.conf (or `luxenos-widgets --settings`)
  WIDGETS=clock cpu ram                 enabled widgets, in order
  WIDGET_<ID>_POS=X,Y                   position in pixels from the top left
  WEATHER_PLACE=Berlin                  label only
  WEATHER_LAT=52.52  WEATHER_LON=13.41  location (no automatic location lookup)
  WEATHER_UNITS=metric|imperial
  WEATHER_MINUTES=30                    how often to refresh (15-360)
  WEATHER_URL=https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code,wind_speed_10m&temperature_unit={temp_unit}&wind_speed_unit={wind_unit}
      any provider that answers in Open-Meteo's JSON format; must be https.
      Open-Meteo needs no account or key. Nothing is sent until you set a location.

Pure helpers are unit tested.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

from . import config, sysinfo, ui

APP_ID = "org.luxenos.Widgets"
KINDS = {
    "clock": "Clock and calendar",
    "weather": "Weather",
    "cpu": "CPU usage",
    "ram": "Memory usage",
    "battery": "Battery",
    "network": "Network speed",
    "storage": "Storage",
    "music": "Music controls",
}
DEFAULT_WIDGETS = "clock cpu ram"
DEFAULT_WEATHER_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                       "&current=temperature_2m,weather_code,wind_speed_10m"
                       "&temperature_unit={temp_unit}&wind_speed_unit={wind_unit}")
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=5&format=json"
WMO = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
       81: "Rain showers", 82: "Violent rain showers", 85: "Snow showers", 86: "Snow showers",
       95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}
WMO_ICON = {0: "weather-clear-symbolic", 1: "weather-few-clouds-symbolic", 2: "weather-few-clouds-symbolic",
            3: "weather-overcast-symbolic", 45: "weather-fog-symbolic", 48: "weather-fog-symbolic"}


def conf_path():
    return os.path.join(config.luxenos_config_dir(), "widgets.conf")


def load():
    return config.read_kv(conf_path())


def enabled(values):
    return [w for w in values.get("WIDGETS", DEFAULT_WIDGETS).split() if w in KINDS]


def position(values, kind, index):
    m = re.match(r"^(\d{1,5}),(\d{1,5})$", values.get("WIDGET_%s_POS" % kind.upper(), ""))
    if m:
        return int(m.group(1)), int(m.group(2))
    return 40, 60 + index * 170


def coordinate(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def weather_url(values):
    lat = coordinate(values.get("WEATHER_LAT"), -90, 90)
    lon = coordinate(values.get("WEATHER_LON"), -180, 180)
    if lat is None or lon is None:
        return None
    template = values.get("WEATHER_URL") or DEFAULT_WEATHER_URL
    if not template.startswith("https://") or "{lat}" not in template or "{lon}" not in template:
        return None
    imperial = values.get("WEATHER_UNITS") == "imperial"
    return (template.replace("{lat}", "%.4f" % lat).replace("{lon}", "%.4f" % lon)
            .replace("{temp_unit}", "fahrenheit" if imperial else "celsius")
            .replace("{wind_unit}", "mph" if imperial else "kmh"))


def parse_weather(text):
    """Open-Meteo JSON -> {temp, unit, code, text, icon, wind, wind_unit} or None."""
    try:
        data = json.loads(text)
        cur = data["current"]
        temp = float(cur["temperature_2m"])
    except (ValueError, KeyError, TypeError):
        return None
    units = data.get("current_units") or {}
    code = cur.get("weather_code")
    code = int(code) if isinstance(code, (int, float)) else None
    icon = WMO_ICON.get(code)
    if icon is None and code is not None:
        icon = ("weather-storm-symbolic" if code >= 95 else "weather-snow-symbolic" if code in range(71, 87)
                else "weather-showers-symbolic")
    return {"temp": temp, "unit": units.get("temperature_2m", "°C"), "code": code,
            "text": WMO.get(code, "Unknown"), "icon": icon or "weather-severe-alert-symbolic",
            "wind": cur.get("wind_speed_10m"), "wind_unit": units.get("wind_speed_10m", "km/h")}


def fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "LuxenOS-widgets/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https only, checked by callers)
        return r.read(256 * 1024).decode("utf-8", "replace")


def geocode(name):
    """[(label, lat, lon)] from Open-Meteo's geocoder (only when you search)."""
    url = GEOCODE_URL.replace("{name}", urllib.parse.quote(name.strip()[:80]))
    data = json.loads(fetch(url))
    out = []
    for r in data.get("results") or []:
        label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
        out.append((label, float(r["latitude"]), float(r["longitude"])))
    return out


def parse_mpris_metadata(meta):
    """{'xesam:title': ..., 'xesam:artist': [...]} -> (title, artist)."""
    title = meta.get("xesam:title") or ""
    artist = meta.get("xesam:artist") or ""
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(a) for a in artist)
    return str(title), str(artist)


def human_rate(n):
    return ui.human_bytes(n) + "/s"


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    Gtk = ui.init(layer_shell="--settings" not in argv)
    from gi.repository import Gio, GLib

    class Widget(Gtk.ApplicationWindow):
        def __init__(self, app, kind, index):
            super().__init__(application=app, title=KINDS[kind])
            self.kind = kind
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            self.set_resizable(False)
            values = load()
            self.x, self.y = position(values, kind, index)
            self.layer = ui.make_layer(self, "luxenos-widget", layer="bottom", anchors=("top", "left"),
                                       margins={"top": self.y, "left": self.x}, keyboard="none")
            self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.card.add_css_class("lx-glass")
            self.card.add_css_class("lx-widget")
            self.card.set_size_request(220, -1)
            self.set_child(self.card)
            self.title = ui.label(KINDS[kind], "lx-dim lx-small")
            self.card.append(self.title)
            if self.layer:
                drag = Gtk.GestureDrag()
                drag.connect("drag-update", self.on_drag)
                drag.connect("drag-end", self.on_drag_end)
                self.card.add_controller(drag)
            getattr(self, "build_" + kind)()

        # -- moving --
        def on_drag(self, _g, dx, dy):
            ui.LayerShell.set_margin(self, ui.LayerShell.Edge.LEFT, max(0, int(self.x + dx)))
            ui.LayerShell.set_margin(self, ui.LayerShell.Edge.TOP, max(0, int(self.y + dy)))

        def on_drag_end(self, _g, dx, dy):
            self.x, self.y = max(0, int(self.x + dx)), max(0, int(self.y + dy))
            config.update_kv(conf_path(), {"WIDGET_%s_POS" % self.kind.upper(): "%d,%d" % (self.x, self.y)},
                             header="# LuxenOS desktop widgets (luxenos-widgets --settings).")

        def every(self, seconds, fn):
            fn()
            GLib.timeout_add_seconds(seconds, lambda: fn() or True)

        def big(self, text=""):
            lbl = ui.label(text, "lx-title")
            self.card.append(lbl)
            return lbl

        # -- widgets --
        def build_clock(self):
            t = self.big()
            d = ui.label("", "lx-dim")
            self.card.append(d)
            cal = Gtk.Calendar()
            cal.add_css_class("lx-calendar")
            self.card.append(cal)
            self.every(15, lambda: (t.set_text(time.strftime("%H:%M")),
                                    d.set_text(time.strftime("%A, %d %B %Y"))))

        def build_weather(self):
            row = Gtk.Box(spacing=8)
            self.w_icon = Gtk.Image.new_from_icon_name("weather-few-clouds-symbolic")
            self.w_icon.set_pixel_size(32)
            row.append(self.w_icon)
            self.w_temp = ui.label("…", "lx-title")
            row.append(self.w_temp)
            self.card.append(row)
            self.w_text = ui.label("", "lx-dim lx-small", wrap=True)
            self.card.append(self.w_text)
            values = load()
            minutes = config.get_int(values, "WEATHER_MINUTES", 30, 15, 360)
            self.every(minutes * 60, self.refresh_weather)

        def refresh_weather(self):
            values = load()
            url = weather_url(values)
            place = values.get("WEATHER_PLACE", "")
            if url is None:
                self.w_temp.set_text("—")
                self.w_text.set_text("Set a location in widget settings")
                return

            def work():
                try:
                    w = parse_weather(fetch(url))
                    err = None if w else "unexpected answer from the weather service"
                except (OSError, ValueError) as e:
                    w, err = None, "offline or unreachable (%s)" % getattr(e, "reason", e)
                GLib.idle_add(show, w, err)

            def show(w, err):
                if w is None:
                    self.w_text.set_text("Weather unavailable: %s" % err)
                    return False
                self.w_icon.set_from_icon_name(w["icon"])
                self.w_temp.set_text("%.0f%s" % (w["temp"], w["unit"]))
                extra = " · wind %s %s" % (w["wind"], w["wind_unit"]) if w["wind"] is not None else ""
                self.w_text.set_text("%s%s%s" % (place + " · " if place else "", w["text"], extra))
                return False

            threading.Thread(target=work, daemon=True).start()

        def meter(self):
            lbl = self.big()
            bar = Gtk.LevelBar(min_value=0, max_value=100)
            self.card.append(bar)
            sub = ui.label("", "lx-dim lx-small")
            self.card.append(sub)
            return lbl, bar, sub

        def build_cpu(self):
            lbl, bar, sub = self.meter()
            sampler = sysinfo.Sampler()
            sub.set_text("%d threads" % sysinfo.cpu_count())

            def tick():
                pct = sampler.sample()["cpu"]
                lbl.set_text("%.0f%%" % pct)
                bar.set_value(pct)
            GLib.timeout_add_seconds(2, lambda: tick() or True)

        def build_ram(self):
            lbl, bar, sub = self.meter()

            def tick():
                m = sysinfo.read_memory() or {}
                total = m.get("total") or 1
                pct = 100.0 * m.get("used", 0) / total
                lbl.set_text("%.0f%%" % pct)
                bar.set_value(pct)
                sub.set_text("%s of %s used" % (ui.human_bytes(m.get("used", 0)), ui.human_bytes(total)))
            self.every(3, tick)

        def build_battery(self):
            lbl, bar, sub = self.meter()

            def tick():
                pct, text = sysinfo.battery_summary(sysinfo.read_batteries())
                if pct is None:
                    lbl.set_text("—")
                    sub.set_text("No battery")
                    return
                lbl.set_text("%d%%" % pct)
                bar.set_value(pct)
                sub.set_text(text)
            self.every(30, tick)

        def build_network(self):
            lbl = self.big()
            sub = ui.label("", "lx-dim lx-small")
            self.card.append(sub)
            sampler = sysinfo.Sampler()

            def tick():
                rx, tx = sampler.sample()["net"]
                lbl.set_text("↓ %s" % human_rate(rx))
                sub.set_text("↑ %s" % human_rate(tx))
            GLib.timeout_add_seconds(2, lambda: tick() or True)
            lbl.set_text("↓ …")

        def build_storage(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.card.append(box)

            def tick():
                while (c := box.get_first_child()) is not None:
                    box.remove(c)
                seen = set()
                for path, name in (("/", "System"), (os.path.expanduser("~"), "Home")):
                    try:
                        st = os.statvfs(path)
                    except OSError:
                        continue
                    if (st.f_fsid, st.f_blocks) in seen:
                        continue
                    seen.add((st.f_fsid, st.f_blocks))
                    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
                    box.append(ui.label("%s: %s free of %s" % (name, ui.human_bytes(free), ui.human_bytes(total)),
                                        "lx-small"))
                    bar = Gtk.LevelBar(min_value=0, max_value=100)
                    bar.set_value(100.0 * (total - free) / total if total else 0)
                    box.append(bar)
            self.every(300, tick)

        def build_music(self):
            self.m_title = ui.label("Nothing playing", "lx-heading")
            self.m_title.set_ellipsize(3)
            self.m_artist = ui.label("", "lx-dim lx-small")
            self.card.append(self.m_title)
            self.card.append(self.m_artist)
            row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
            for icon, method, tip in (("media-skip-backward-symbolic", "Previous", "Previous track"),
                                      ("media-playback-start-symbolic", "PlayPause", "Play or pause"),
                                      ("media-skip-forward-symbolic", "Next", "Next track")):
                row.append(ui.icon_button(icon, tip, lambda m=method: self.mpris_call(m)))
            self.card.append(row)
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.every(3, self.mpris_refresh)

        def mpris_player(self):
            try:
                names = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                           "ListNames", None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            except GLib.Error:
                return None
            players = sorted(n for n in names if n.startswith("org.mpris.MediaPlayer2."))
            playing = None
            for n in players:
                if self.mpris_get(n, "PlaybackStatus") == "Playing":
                    playing = n
                    break
            return playing or (players[0] if players else None)

        def mpris_get(self, name, prop):
            try:
                v = self.bus.call_sync(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                       GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", prop)), None,
                                       Gio.DBusCallFlags.NONE, 1000, None)
                return v.unpack()[0]
            except GLib.Error:
                return None

        def mpris_refresh(self):
            name = self.mpris_player()
            self.player = name
            if not name:
                self.m_title.set_text("Nothing playing")
                self.m_artist.set_text("")
                return
            title, artist = parse_mpris_metadata(self.mpris_get(name, "Metadata") or {})
            self.m_title.set_text(title or name.split(".", 3)[-1])
            self.m_artist.set_text(artist)

        def mpris_call(self, method):
            if not getattr(self, "player", None):
                return
            try:
                self.bus.call_sync(self.player, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player",
                                   method, None, None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error as e:
                print("luxenos-widgets: %s" % e.message, file=sys.stderr)
            GLib.timeout_add(300, lambda: self.mpris_refresh() or False)

    class Settings(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Desktop widgets")
            self.set_default_size(520, 600)
            values = load()
            on = enabled(values)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(16)
            box.append(ui.label("Widgets", "lx-title"))
            box.append(ui.label("Drag a widget on the desktop to move it. Widgets sit behind windows.",
                                "lx-dim lx-small", wrap=True))
            self.checks = {}
            for kind, title in KINDS.items():
                c = Gtk.CheckButton(label=title, active=kind in on)
                self.checks[kind] = c
                box.append(c)
            box.append(ui.label("Weather", "lx-heading"))
            box.append(ui.label("From Open-Meteo (no account). Your location is sent only to the weather service, "
                                "only when a location is set.", "lx-dim lx-small", wrap=True))
            find = Gtk.Box(spacing=6)
            self.place = Gtk.Entry(text=values.get("WEATHER_PLACE", ""), placeholder_text="City", hexpand=True)
            ui.accessible_label(self.place, "City for the weather")
            find.append(self.place)
            fb = Gtk.Button(label="Find")
            fb.connect("clicked", lambda *_: self.find_place())
            find.append(fb)
            box.append(find)
            self.results = Gtk.DropDown.new_from_strings([])
            self.results.set_visible(False)
            self.results.connect("notify::selected", lambda *_: self.pick_place())
            box.append(self.results)
            coords = Gtk.Box(spacing=6)
            self.lat = Gtk.Entry(text=values.get("WEATHER_LAT", ""), placeholder_text="Latitude")
            self.lon = Gtk.Entry(text=values.get("WEATHER_LON", ""), placeholder_text="Longitude")
            for e, t in ((self.lat, "Latitude"), (self.lon, "Longitude")):
                ui.accessible_label(e, t)
                coords.append(e)
            box.append(coords)
            self.units = Gtk.DropDown.new_from_strings(["Metric (°C, km/h)", "Imperial (°F, mph)"])
            self.units.set_selected(1 if values.get("WEATHER_UNITS") == "imperial" else 0)
            box.append(self.units)
            self.msg = ui.label("", "lx-small", wrap=True)
            box.append(self.msg)
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            reset = Gtk.Button(label="Reset positions")
            reset.connect("clicked", lambda *_: self.save(reset_positions=True))
            save = Gtk.Button(label="Save")
            save.add_css_class("suggested-action")
            save.connect("clicked", lambda *_: self.save())
            row.append(reset)
            row.append(save)
            box.append(row)
            self.found = []
            sw = Gtk.ScrolledWindow()
            sw.set_child(box)
            self.set_child(sw)

        def find_place(self):
            name = self.place.get_text().strip()
            if not name:
                return
            self.msg.set_text("Searching…")

            def work():
                try:
                    res, err = geocode(name), None
                except (OSError, ValueError) as e:
                    res, err = [], str(e)
                GLib.idle_add(show, res, err)

            def show(res, err):
                self.found = res
                if err or not res:
                    self.msg.set_text("No place found%s" % (": " + err if err else ""))
                    return False
                self.msg.set_text("")
                self.results.set_model(Gtk.StringList.new([r[0] for r in res]))
                self.results.set_visible(True)
                self.results.set_selected(0)
                self.pick_place()
                return False

            threading.Thread(target=work, daemon=True).start()

        def pick_place(self):
            i = self.results.get_selected()
            if 0 <= i < len(self.found):
                label, lat, lon = self.found[i]
                self.place.set_text(label.split(",")[0])
                self.lat.set_text("%.4f" % lat)
                self.lon.set_text("%.4f" % lon)

        def save(self, reset_positions=False):
            lat, lon = self.lat.get_text().strip(), self.lon.get_text().strip()
            if (lat or lon) and (coordinate(lat, -90, 90) is None or coordinate(lon, -180, 180) is None):
                self.msg.set_text("Latitude must be -90…90 and longitude -180…180.")
                return
            changes = {"WIDGETS": " ".join(k for k, c in self.checks.items() if c.get_active()),
                       "WEATHER_PLACE": " ".join(self.place.get_text().split())[:60] or None,
                       "WEATHER_LAT": lat or None, "WEATHER_LON": lon or None,
                       "WEATHER_UNITS": "imperial" if self.units.get_selected() == 1 else "metric"}
            remove = ["WIDGET_%s_POS" % k.upper() for k in KINDS] if reset_positions else []
            config.update_kv(conf_path(), changes, remove=remove,
                             header="# LuxenOS desktop widgets (luxenos-widgets --settings).")
            ui.spawn(["luxenos-widgets", "--restart"])
            self.msg.set_text("Saved. Widgets were restarted.")

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.widgets = []
            for name, text in (("settings", "Open widget settings"), ("restart", "Restart the widgets"),
                               ("stop", "Remove the widgets from the desktop")):
                self.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, text, None)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()
            self.theme_monitor = ui.watch_theme()

        def start_widgets(self):
            for w in self.widgets:
                w.destroy()
            self.widgets = [Widget(self, kind, i) for i, kind in enumerate(enabled(load()))]
            for w in self.widgets:
                w.present()
            if self.widgets:
                self.hold_once()

        def hold_once(self):
            if not getattr(self, "held", False):
                self.hold()
                self.held = True

        def do_command_line(self, cmdline):
            opts = cmdline.get_options_dict().end().unpack()
            if opts.get("settings"):
                (next((w for w in self.get_windows() if isinstance(w, Settings)), None) or Settings(self)).present()
            elif opts.get("stop"):
                for w in self.widgets:
                    w.destroy()
                self.widgets = []
                if getattr(self, "held", False):
                    self.release()
                    self.held = False
            else:  # start or --restart
                self.start_widgets()
            return 0

    return App().run(argv)
