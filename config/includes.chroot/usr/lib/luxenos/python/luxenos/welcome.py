"""First-boot welcome guide.

Started by the Sway config with --first-run at every login, it shows itself
only once per user on an installed system: never on the live USB (installer
kiosk or live desktop), never again after Finish or Skip
(~/.config/luxenos/welcome-done). It can be reopened any time from the
launcher ("Welcome to LuxenOS") or Settings.

Nothing is changed just by paging through: every setting is applied only
when its own button is pressed, through the same tools the rest of LuxenOS
uses (sway input config fragment, timedatectl, luxenos-theme, luxenos-glass,
the Software Center), so existing preferences are never overwritten on a
later login. No step needs the internet; network-only steps say so.
"""

import os
import re
import shutil
import subprocess
import sys

from . import config, os_version, pkg, themes, ui

APP_ID = "org.luxenos.Welcome"
LAYOUT_RE = re.compile(r"^[a-z]{2,8}$")
ACCENTS = (("#7aa2ff", "Blue"), ("#9d7aff", "Violet"), ("#e06c9f", "Pink"), ("#e5a24a", "Amber"),
           ("#4fb38a", "Green"), ("#5e81ac", "Slate"))
SHORTCUTS = (
    ("Super + Space", "Search apps, files and actions"),
    ("Super + Return", "Terminal"),
    ("Super + Tab", "Workspace overview"),
    ("Super + N", "Quick Settings"),
    ("Super + 1 … 4", "Switch workspace"),
    ("Super + Shift + 1 … 4", "Move window to workspace"),
    ("Super + F", "Fullscreen"),
    ("Super + Shift + Space", "Float / tile window"),
    ("Super + Shift + Q", "Close window"),
    ("Super + L", "Lock screen"),
    ("Super + Shift + S", "Screenshot of an area"),
    ("Ctrl + Shift + Esc", "System Monitor"),
)


def marker_path():
    return os.path.join(config.luxenos_config_dir(), "welcome-done")


def should_autostart(cmdline_path="/proc/cmdline"):
    return not config.is_live_session(cmdline_path) and not os.path.exists(marker_path())


def mark_done():
    config.update_kv(marker_path(), {"DONE": "yes", "LUXENOS_VERSION": os_version().replace(" ", "_")},
                     header="# The welcome guide ran. Delete this file to see it again at login.")


def parse_layouts(text):
    """evdev.lst '! layout' section -> [(code, description)]."""
    out, active = [], False
    for line in text.splitlines():
        if line.startswith("!"):
            active = line.strip() == "! layout"
            continue
        if active and line.strip():
            code, _, desc = line.strip().partition(" ")
            if LAYOUT_RE.match(code):
                out.append((code, desc.strip()))
    return sorted(out, key=lambda t: t[1].lower())


def keyboard_fragment(layout):
    if not LAYOUT_RE.match(layout):
        raise ValueError("invalid keyboard layout")
    return "input type:keyboard xkb_layout %s" % layout


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    if "--first-run" in argv and not should_autostart():
        return 0
    Gtk = ui.init()
    from gi.repository import Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Welcome to LuxenOS")
            self.set_default_size(820, 600)
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            skip = Gtk.Button(label="Skip guide")
            skip.connect("clicked", lambda *_: self.finish())
            ui.accessible_label(skip, "Skip the welcome guide; it can be reopened from the launcher")
            header.pack_end(skip)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.set_child(root)
            self.stack = Gtk.Stack(vexpand=True)
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
            self.stack.set_transition_duration(ui.transition_ms(200))
            root.append(self.stack)
            self.pages = []
            self.page_welcome()
            self.page_region()
            self.page_network()
            self.page_appearance()
            self.page_software()
            self.page_shortcuts()
            self.page_done()

            nav = Gtk.Box(spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(nav, "set_margin_" + m)(14)
            self.back = Gtk.Button(label="Back")
            self.back.connect("clicked", lambda *_: self.go(-1))
            nav.append(self.back)
            self.dots = ui.label("", "lx-dim")
            self.dots.set_hexpand(True)
            self.dots.set_xalign(0.5)
            nav.append(self.dots)
            self.next = Gtk.Button(label="Next")
            self.next.add_css_class("suggested-action")
            self.next.connect("clicked", lambda *_: self.go(1))
            nav.append(self.next)
            root.append(nav)
            self.index = 0
            self.go(0)

        # -- helpers --
        def page(self, title, subtitle):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            for m in ("start", "end"):
                getattr(box, "set_margin_" + m)(40)
            box.set_margin_top(24)
            box.append(ui.label(title, "lx-hero-title", wrap=True))
            if subtitle:
                box.append(ui.label(subtitle, "lx-dim", wrap=True))
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(box)
            self.stack.add_named(sw, "p%d" % len(self.pages))
            self.pages.append(title)
            return box

        def result(self, box):
            lbl = ui.label("", "lx-small", wrap=True)
            box.append(lbl)
            return lbl

        def say(self, lbl, text, ok=True):
            lbl.set_text(text)
            lbl.remove_css_class("lx-crit" if ok else "lx-accent")
            lbl.add_css_class("lx-accent" if ok else "lx-crit")

        def run(self, argv, lbl, ok_text):
            try:
                p = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError) as e:
                self.say(lbl, str(e), False)
                return False
            if p.returncode != 0:
                self.say(lbl, (p.stderr or p.stdout).strip().splitlines()[-1:] and
                         (p.stderr or p.stdout).strip().splitlines()[-1] or "failed", False)
                return False
            self.say(lbl, ok_text)
            return True

        def go(self, delta):
            if delta > 0 and self.index == len(self.pages) - 1:
                self.finish()
                return
            self.index = max(0, min(len(self.pages) - 1, self.index + delta))
            self.stack.set_visible_child_name("p%d" % self.index)
            self.back.set_sensitive(self.index > 0)
            self.next.set_label("Finish" if self.index == len(self.pages) - 1 else "Next")
            self.dots.set_text("Step %d of %d · %s" % (self.index + 1, len(self.pages), self.pages[self.index]))
            self.set_default_widget(self.next)

        def finish(self):
            try:
                mark_done()
            except OSError as e:
                print("luxenos-welcome: cannot record completion: %s" % e, file=sys.stderr)
            self.close()

        # -- pages --
        def page_welcome(self):
            box = self.page("Welcome to LuxenOS", "LuxenOS %s · Debian underneath, a liquid-glass Sway desktop, "
                            "and Android apps through Waydroid." % os_version())
            logo = "/usr/share/luxenos/logo.png"
            if os.path.exists(logo):
                img = Gtk.Picture.new_for_filename(logo)
                img.set_can_shrink(True)
                img.set_size_request(-1, 120)
                box.append(img)
            box.append(ui.label("This short guide helps with the first settings. Every step is optional: "
                                "nothing changes until you press a button on a page, and you can skip the "
                                "whole guide. Reopen it any time: search “Welcome” with Super+Space.",
                                None, wrap=True))
            box.append(ui.label("Use Tab and Enter, or the mouse. Esc does nothing here, so nothing is lost by "
                                "accident.", "lx-dim lx-small", wrap=True))

        def page_region(self):
            box = self.page("Keyboard and region", "Choose the keyboard layout and the time zone.")
            layouts = []
            try:
                with open("/usr/share/X11/xkb/rules/evdev.lst", encoding="utf-8") as f:
                    layouts = parse_layouts(f.read())
            except OSError:
                pass
            row = Gtk.Box(spacing=8)
            row.append(ui.label("Keyboard layout", "lx-heading"))
            res = None
            if layouts:
                names = Gtk.StringList.new(["%s (%s)" % (d, c) for c, d in layouts])
                dd = Gtk.DropDown(model=names)
                dd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
                dd.set_enable_search(True)
                dd.set_hexpand(True)
                ui.accessible_label(dd, "Keyboard layout")
                codes = [c for c, _ in layouts]
                if "us" in codes:
                    dd.set_selected(codes.index("us"))
                row.append(dd)
                btn = Gtk.Button(label="Use this layout")
                row.append(btn)
                box.append(row)
                res = self.result(box)

                def apply_layout(*_):
                    code = codes[dd.get_selected()]
                    path = os.path.join(config.luxenos_config_dir(), "sway.d", "10-keyboard.conf")
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("# Written by the LuxenOS welcome guide.\n%s\n" % keyboard_fragment(code))
                    if shutil.which("swaymsg"):
                        self.run(["swaymsg", keyboard_fragment(code)], res,
                                 "Keyboard layout set to %s (saved for future logins)." % code)
                    else:
                        self.say(res, "Saved; it applies at the next login.")

                btn.connect("clicked", apply_layout)
            else:
                box.append(ui.label("Keyboard layouts are not available (xkb-data missing).", "lx-dim"))

            row = Gtk.Box(spacing=8)
            row.append(ui.label("Time zone", "lx-heading"))
            zones = []
            if shutil.which("timedatectl"):
                try:
                    zones = subprocess.run(["timedatectl", "list-timezones"], capture_output=True, text=True,
                                           timeout=10, check=False).stdout.split()
                except (OSError, subprocess.SubprocessError):
                    zones = []
            if zones:
                zl = Gtk.StringList.new(zones)
                zd = Gtk.DropDown(model=zl)
                zd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
                zd.set_enable_search(True)
                zd.set_hexpand(True)
                ui.accessible_label(zd, "Time zone")
                try:
                    cur = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
                    if cur in zones:
                        zd.set_selected(zones.index(cur))
                except IndexError:
                    pass
                row.append(zd)
                zb = Gtk.Button(label="Set time zone")
                row.append(zb)
                box.append(row)
                zres = self.result(box)
                zb.connect("clicked", lambda *_: self.run(["timedatectl", "set-timezone", zones[zd.get_selected()]],
                                                          zres, "Time zone set to %s." % zones[zd.get_selected()]))
                box.append(ui.label("Changing the time zone may ask for your password.", "lx-dim lx-small"))
            else:
                box.append(ui.label("Time zones cannot be changed here (timedatectl unavailable).", "lx-dim"))
            box.append(ui.label("The system language was chosen during installation. To change it later, run "
                                "“sudo dpkg-reconfigure locales” in a terminal.", "lx-dim lx-small", wrap=True))

        def page_network(self):
            box = self.page("Network", "LuxenOS works offline. A connection is only needed for updates and "
                                       "new software.")
            status = self.result(box)
            wifi = False
            text = "NetworkManager is not running, so connections cannot be managed."
            if shutil.which("nmcli"):
                try:
                    conn = subprocess.run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"], capture_output=True,
                                          text=True, timeout=5, check=False).stdout.strip()
                    devs = subprocess.run(["nmcli", "-t", "-f", "TYPE", "device"], capture_output=True,
                                          text=True, timeout=5, check=False).stdout.split()
                    wifi = "wifi" in devs
                    text = {"full": "You are online.", "limited": "Connected, but the internet is not reachable "
                            "(a sign-in page may be needed).", "portal": "A sign-in page is required: open "
                            "Firefox to sign in.", "none": "Not connected."}.get(conn, "Connection state: %s" % conn)
                except (OSError, subprocess.SubprocessError):
                    pass
            self.say(status, text, "online" in text or "Connected" in text)
            if wifi:
                box.append(ui.label("To join a Wi-Fi network, click the network icon in the top bar, or open the "
                                    "connection editor below.", None, wrap=True))
            else:
                box.append(ui.label("No Wi-Fi adapter was found. Plug in an Ethernet cable (it connects "
                                    "automatically) or a USB Wi-Fi adapter.", None, wrap=True))
            b = Gtk.Button(label="Open network settings")
            b.set_halign(Gtk.Align.START)
            b.connect("clicked", lambda *_: ui.spawn(["luxenos-network"]))
            box.append(b)

        def page_appearance(self):
            box = self.page("Appearance", "Pick a look. Liquid Glass strength and Reduce motion are under "
                                          "Settings → Accessibility.")
            res = self.result(box)
            packs = themes.available()
            group = None
            for ident, (values, _p) in sorted(packs.items()):
                b = Gtk.CheckButton(label="%s — %s" % (values["THEME_NAME"], values.get("THEME_DESCRIPTION", "")))
                if group is not None:
                    b.set_group(group)
                group = group or b
                if ident == themes.current():
                    b.set_active(True)
                b.connect("toggled", lambda btn, i=ident: btn.get_active() and self.apply_theme(i, res))
                box.append(b)
            if not packs:
                box.append(ui.label("No theme packs are installed.", "lx-dim"))
            box.append(ui.label("Accent colour", "lx-heading"))
            row = Gtk.Box(spacing=6)
            for hexc, name in ACCENTS:
                btn = Gtk.Button(label=name)
                btn.connect("clicked", lambda _b, h=hexc, n=name: self.run(
                    ["luxenos-glass", "set", "GLASS_ACCENT", h], res, "Accent colour: %s." % n))
                row.append(btn)
            box.append(row)

        def apply_theme(self, ident, res):
            try:
                themes.apply(ident)
                self.say(res, "Applied. You can switch again any time in Settings → Appearance.")
            except (KeyError, RuntimeError, OSError) as e:
                self.say(res, str(e), False)

        def page_software(self):
            box = self.page("Updates and apps", "Optional, and only with an internet connection.")
            b = Gtk.Button(label="Check for updates")
            b.set_halign(Gtk.Align.START)
            b.connect("clicked", lambda *_: ui.spawn(["luxenos-software", "--updates"]))
            box.append(b)
            box.append(ui.label("Opens the Software Center on its Updates page; refresh the catalogue there "
                                "first. Updates are also installed automatically at night (see "
                                "luxenos-update-settings).", "lx-dim lx-small", wrap=True))
            box.append(ui.label("Suggested apps", "lx-heading"))
            self.picks = []
            grid = Gtk.Grid(column_spacing=12, row_spacing=4)
            for i, (name, desc) in enumerate(pkg.SUGGESTED):
                c = Gtk.CheckButton(label="%s — %s" % (name, desc))
                c.pkg = name
                self.picks.append(c)
                grid.attach(c, i % 2, i // 2, 1, 1)
            box.append(grid)
            ib = Gtk.Button(label="Install selected…")
            ib.set_halign(Gtk.Align.START)
            ib.connect("clicked", lambda *_: ui.spawn(["luxenos-software", "--install"] +
                                                      [c.pkg for c in self.picks if c.get_active()]))
            box.append(ib)
            box.append(ui.label("The Software Center shows what will be installed and asks for your password "
                                "before anything changes.", "lx-dim lx-small", wrap=True))

        def page_shortcuts(self):
            box = self.page("Getting around", "The Super key is the one with the Windows logo.")
            grid = Gtk.Grid(column_spacing=16, row_spacing=8)
            for i, (keys, what) in enumerate(SHORTCUTS):
                k = ui.label(keys, "lx-keycap")
                k.set_halign(Gtk.Align.START)
                grid.attach(k, (i % 2) * 2, i // 2, 1, 1)
                grid.attach(ui.label(what), (i % 2) * 2 + 1, i // 2, 1, 1)
            box.append(grid)
            box.append(ui.label("The dock at the bottom shows your apps: click to open or switch, click again "
                                "to minimize, right-click for every window. The top bar shows workspaces, the "
                                "clock, and Quick Settings (slider icon).", None, wrap=True))

        def page_done(self):
            box = self.page("You're all set", "Enjoy LuxenOS.")
            box.append(ui.label("Find this guide again by searching “Welcome” (Super+Space) or in Settings. "
                                "If the desktop ever fails to start, press Ctrl+Alt+F2, log in and run "
                                "“sudo luxenos-restore” to go back to a restore point.", None, wrap=True))

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.add_main_option("first-run", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                                 "Show only if it has not run for this user", None)
            self.add_main_option("force", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                                 "Show even on the live system", None)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv)
