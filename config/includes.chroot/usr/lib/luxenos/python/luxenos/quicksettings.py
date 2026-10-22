"""Quick Settings panel (Waybar slider icon, Super+N).

A glass panel under the bar with the controls people reach for most. Every
control shows the real state read from its service (see luxenos.quick), is
re-read every 2 seconds while the panel is open (so changes made elsewhere,
e.g. an Fn key or nm-applet, show up), and is disabled with the reason when
the hardware or service is missing. Nothing needs a password except what the
service itself asks for. Running the command again closes the panel.
"""

import sys
import threading
import time

from . import quick, ui

APP_ID = "org.luxenos.QuickSettings"
POLL_MS = 2000
PROFILES = (("powersave", "Power saver"), ("balanced", "Balanced"), ("performance", "Performance"))


def main(argv=None):
    Gtk = ui.init(layer_shell=True)
    from gi.repository import Gio, GLib, Pango

    class Tile(Gtk.ToggleButton):
        def __init__(self, backend, icon):
            super().__init__()
            self.backend = backend
            self.add_css_class("lx-tile")
            box = Gtk.Box(spacing=10)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(20)
            box.append(img)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.title = ui.label(backend.label, "lx-heading")
            self.sub = ui.label("…", "lx-dim lx-small")
            self.sub.set_ellipsize(Pango.EllipsizeMode.END)
            self.sub.set_max_width_chars(16)
            vb.append(self.title)
            vb.append(self.sub)
            box.append(vb)
            self.set_child(box)
            self.set_hexpand(True)
            self.updating = False

        def show_state(self, avail, reason, on, text):
            self.updating = True
            self.set_sensitive(avail)
            if not avail:
                self.set_active(False)
                self.sub.set_text(reason)
                state = "unavailable, %s" % reason
            else:
                self.set_active(bool(on))
                self.sub.set_text(text)
                state = "%s, %s" % ("on" if on else "off", text)
            if self.get_active():
                self.add_css_class("on")
            else:
                self.remove_css_class("on")
            ui.accessible_label(self, "%s: %s" % (self.backend.label, state))
            self.updating = False

    class Slider(Gtk.Box):
        def __init__(self, backend, icon, on_icon_click=None):
            super().__init__(spacing=8)
            self.backend = backend
            self.icon = Gtk.Button.new_from_icon_name(icon)
            self.icon.add_css_class("flat")
            if on_icon_click:
                self.icon.connect("clicked", lambda *_: on_icon_click())
            else:
                self.icon.set_can_target(False)
                self.icon.set_focusable(False)
            self.append(self.icon)
            self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            self.scale.set_hexpand(True)
            self.scale.set_draw_value(False)
            ui.accessible_label(self.scale, backend.label)
            self.append(self.scale)
            self.value = ui.label("", "lx-dim lx-small")
            self.value.set_width_chars(5)
            self.append(self.value)
            self.reason = ui.label("", "lx-dim lx-small")
            self.updating = False
            self.last_user_change = 0.0

        def show_state(self, avail, reason, level):
            self.set_sensitive(avail)
            if not avail:
                self.value.set_text("")
                self.scale.set_tooltip_text(reason)
                ui.accessible_label(self.scale, "%s: unavailable, %s" % (self.backend.label, reason))
                return
            if time.monotonic() - self.last_user_change < 1.5:
                return  # don't fight the user while they drag
            self.updating = True
            self.scale.set_value(level)
            self.value.set_text("%d%%" % level)
            ui.accessible_label(self.scale, "%s %d percent" % (self.backend.label, level))
            self.updating = False

    class Panel(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Quick Settings")
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            ui.make_layer(self, "luxenos-quicksettings", layer="overlay", anchors=("top", "right"),
                          margins={"top": 6, "right": 8}, keyboard="on_demand", exclusive_zone=0)
            self.b = {
                "wifi": quick.WiFi(), "bt": quick.Bluetooth(), "night": quick.NightLight(),
                "dnd": quick.DoNotDisturb(), "vol": quick.Volume(), "bright": quick.Brightness(),
                "bat": quick.Battery(), "power": quick.PowerProfile(),
            }
            self.busy = False
            self.was_active = False

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            root.add_css_class("lx-glass")
            root.set_size_request(360, -1)
            self.set_child(root)

            head = Gtk.Box(spacing=8)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            vb.append(ui.label("Quick Settings", "lx-title"))
            self.battery = ui.label("", "lx-dim lx-small")
            vb.append(self.battery)
            head.append(vb)
            head.append(ui.icon_button("preferences-system-symbolic", "All Settings",
                                       lambda: self.launch(["luxenos-settings"])))
            head.append(ui.icon_button("window-close-symbolic", "Close", self.close))
            root.append(head)

            grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
            self.tiles = {
                "wifi": Tile(self.b["wifi"], "network-wireless-symbolic"),
                "bt": Tile(self.b["bt"], "bluetooth-active-symbolic"),
                "night": Tile(self.b["night"], "night-light-symbolic"),
                "dnd": Tile(self.b["dnd"], "notifications-disabled-symbolic"),
            }
            for i, key in enumerate(("wifi", "bt", "night", "dnd")):
                t = self.tiles[key]
                t.connect("toggled", self.on_tile, key)
                grid.attach(t, i % 2, i // 2, 1, 1)
            root.append(grid)

            self.vol = Slider(self.b["vol"], "audio-volume-high-symbolic", self.toggle_mute)
            ui.accessible_label(self.vol.icon, "Mute or unmute")
            self.vol.scale.connect("value-changed", self.on_slider, "vol")
            root.append(self.vol)
            self.bright = Slider(self.b["bright"], "display-brightness-symbolic")
            self.bright.scale.connect("value-changed", self.on_slider, "bright")
            root.append(self.bright)

            prow = Gtk.Box(spacing=6)
            prow.append(ui.label("Power", "lx-dim"))
            self.profile_buttons = {}
            group = None
            for key, text in PROFILES:
                btn = Gtk.ToggleButton(label=text)
                btn.add_css_class("lx-tile")
                if group is not None:
                    btn.set_group(group)
                group = group or btn
                btn.connect("toggled", self.on_profile, key)
                btn.set_hexpand(True)
                self.profile_buttons[key] = btn
                prow.append(btn)
            self.profile_row = prow
            self.profile_updating = False
            root.append(prow)

            links = Gtk.Box(spacing=6, homogeneous=True)
            for text, argv in (("Network…", ["luxenos-network"]), ("Sound…", ["pavucontrol-qt"]),
                               ("Settings…", ["luxenos-settings"])):
                b = Gtk.Button(label=text)
                b.connect("clicked", lambda _b, a=argv: self.launch(a))
                links.append(b)
            root.append(links)

            self.status = ui.label("", "lx-small", wrap=True)
            self.status.set_visible(False)
            root.append(self.status)

            ui.add_escape_handler(self, self.close)
            self.connect("notify::is-active", self.on_active)
            self.poll()
            self.timer = GLib.timeout_add(POLL_MS, self.poll)
            self.connect("close-request", self.on_close)

        def on_close(self, *_):
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            return False

        def on_active(self, *_):
            if self.is_active():
                self.was_active = True
            elif self.was_active:
                self.close()

        def launch(self, argv):
            if ui.spawn(argv):
                self.close()
            else:
                self.error("%s is not available" % argv[0])

        def error(self, text):
            self.status.set_text(text)
            self.status.add_css_class("lx-crit")
            self.status.set_visible(bool(text))

        # -- state --
        def poll(self):
            if not self.busy:
                self.busy = True
                threading.Thread(target=self.collect, daemon=True).start()
            return True

        def collect(self):
            st = {}
            for key, backend in self.b.items():
                try:
                    avail, reason = backend.available()
                    value = backend.state() if avail else (None, reason)
                    st[key] = (avail, reason, value)
                except (quick.Unavailable, RuntimeError, ValueError, OSError) as e:
                    st[key] = (False, str(e), (None, ""))
            GLib.idle_add(self.apply_state, st)

        def apply_state(self, st):
            self.busy = False
            for key in ("wifi", "bt", "night", "dnd"):
                avail, reason, (on, text) = st[key]
                self.tiles[key].show_state(avail, reason, on, text)
            avail, reason, value = st["vol"]
            if avail:
                level, muted = value
                self.vol.show_state(True, "", level)
                self.vol.icon.set_icon_name("audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic")
                ui.accessible_label(self.vol.icon, "Unmute" if muted else "Mute")
            else:
                self.vol.show_state(False, reason, 0)
            avail, reason, value = st["bright"]
            self.bright.show_state(avail, reason, value if avail else 0)
            avail, reason, value = st["bat"]
            self.battery.set_text(value[1] if avail else "No battery (on mains power)")
            avail, reason, value = st["power"]
            self.profile_row.set_sensitive(avail)
            if avail:
                self.profile_updating = True
                btn = self.profile_buttons.get(value[0])
                if btn is not None:
                    btn.set_active(True)
                self.profile_updating = False
            else:
                self.profile_row.set_tooltip_text(reason)
            return False

        def run_action(self, fn, *args):
            def work():
                try:
                    fn(*args)
                    GLib.idle_add(self.error, "")
                except (quick.Unavailable, RuntimeError, OSError, ValueError) as e:
                    GLib.idle_add(self.error, str(e))
                GLib.idle_add(self.poll)
            threading.Thread(target=work, daemon=True).start()

        def on_tile(self, tile, key):
            if tile.updating:
                return
            self.run_action(tile.backend.set, tile.get_active())

        def on_slider(self, scale, key):
            slider = self.vol if key == "vol" else self.bright
            if slider.updating:
                return
            slider.last_user_change = time.monotonic()
            value = int(scale.get_value())
            slider.value.set_text("%d%%" % value)
            self.run_action(slider.backend.set_level, value)

        def toggle_mute(self):
            def work():
                try:
                    _, muted = self.b["vol"].state()
                    self.b["vol"].set_mute(not muted)
                except (quick.Unavailable, RuntimeError, ValueError) as e:
                    GLib.idle_add(self.error, str(e))
                GLib.idle_add(self.poll)
            threading.Thread(target=work, daemon=True).start()

        def on_profile(self, btn, key):
            if self.profile_updating or not btn.get_active():
                return
            self.run_action(lambda: quick.run(["luxenos-power-profile-cycle", key], timeout=60))

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window = None

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_command_line(self, _cmdline):
            if self.window is not None and self.window.get_visible():
                self.window.close()
                self.window = None
                return 0
            self.window = Panel(self)
            self.window.present()
            return 0

    return App().run(argv if argv is not None else sys.argv)
