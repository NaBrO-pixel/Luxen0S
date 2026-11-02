"""LuxenOS Update Manager.

One place for every kind of update: Debian packages (security updates shown
separately), Flatpak applications, firmware (fwupd) and Android (Waydroid).
It is a front end for the existing updater infrastructure, not a second one:

  * reading is unprivileged (luxenos.updates: apt simulation, flatpak, fwupd,
    apt history);
  * every change runs through /usr/libexec/luxenos/luxenos-pkg-root via pkexec
    (administrator password), which holds the one package lock that the
    Software Center, the .deb installer and the automatic updater
    (luxenos-updater, luxenos-auto-update.timer) share, so package operations
    never overlap; per-user Flatpaks are updated as the user;
  * the helper makes a restore point (and on Btrfs a snapshot) first, as
    configured under Settings;
  * firmware and Android updates, and any update that would remove a
    package, ask for confirmation first.

Command line: luxenos-update-manager [--notify]
  --notify   no window: send a desktop notification if updates are waiting
             (run daily by luxenos-update-notify.timer, a systemd user timer)
"""

import os
import shutil
import subprocess
import sys
import threading

from . import config, pkg, ui, updates

APP_ID = "org.luxenos.UpdateManager"
SOURCE_TITLES = {
    "security": ("Security updates", "security-high-symbolic"),
    "debian": ("Debian packages", "system-software-update-symbolic"),
    "android": ("Android (Waydroid)", "phone-symbolic"),
    "flatpak": ("Flatpak applications", "application-x-addon-symbolic"),
    "firmware": ("Firmware", "drive-harddisk-system-symbolic"),
}


def notify_main():
    """--notify: one desktop notification, no window."""
    sched = updates.schedule()
    if sched.get("ENABLE_NOTIFICATIONS") != "yes" or sched.get("NOTIFY_AVAILABLE") != "yes":
        return 0
    if config.is_live_session() or not shutil.which("notify-send"):
        return 0
    upgrades, _new, _removed, errors = updates.apt_upgrades()
    if errors:
        print("luxenos-update-manager: %s" % "; ".join(errors), file=sys.stderr)
        return 1
    text = updates.notify_summary(upgrades)
    if not text:
        return 0
    try:
        p = subprocess.run(["notify-send", "-a", "Update Manager", "-i", "system-software-update",
                            "-A", "open=Open Update Manager", "Software updates", text],
                           capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0
    if p.stdout.strip() == "open":
        ui.spawn(["luxenos-update-manager"])
    return 0


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    if "--notify" in argv[1:]:
        return notify_main()

    Gtk = ui.init()
    from gi.repository import Gio, GLib, Pango

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Update Manager")
            self.set_default_size(960, 680)
            self.busy = False
            self.found = {}
            self.errors = {}
            self.log_lines = []

            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(ui.transition_ms(150))
            header.set_title_widget(Gtk.StackSwitcher(stack=self.stack))
            self.check_btn = Gtk.Button(label="Check for updates")
            self.check_btn.add_css_class("suggested-action")
            self.check_btn.connect("clicked", lambda *_: self.check_online())
            header.pack_start(self.check_btn)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "top"):
                getattr(self.banners, "set_margin_" + m)(10)
            outer.append(self.banners)
            outer.append(self.stack)
            outer.append(self.build_progress())
            self.set_child(outer)

            self.build_updates_page()
            self.build_history_page()
            self.build_settings_page()
            self.connect("close-request", self.on_close)
            if config.is_live_session():
                self.banner("This is the live system: updates are installed on the installed LuxenOS.", "warning")
            if pkg.dpkg_interrupted():
                self.banner("A previous package operation was interrupted. Use “Repair” before updating.", "warning",
                            ("Repair", lambda: self.run_root(["repair"], "Repairing")))
            self.scan()

        # -- layout --
        def banner(self, text, kind="info", action=None):
            box = Gtk.Box(spacing=8)
            box.add_css_class("lx-banner")
            box.add_css_class(kind)
            lbl = ui.label(text, None, wrap=True)
            lbl.set_hexpand(True)
            box.append(lbl)
            if action:
                b = Gtk.Button(label=action[0])
                b.connect("clicked", lambda *_: action[1]())
                box.append(b)
            self.banners.append(box)

        def build_progress(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "bottom"):
                getattr(box, "set_margin_" + m)(10)
            self.status = ui.label("", "lx-small", wrap=True)
            box.append(self.status)
            self.progress = Gtk.ProgressBar(show_text=False)
            self.progress.set_visible(False)
            box.append(self.progress)
            exp = Gtk.Expander(label="Details")
            self.output = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
            sw = Gtk.ScrolledWindow(min_content_height=120)
            sw.set_child(self.output)
            exp.set_child(sw)
            box.append(exp)
            return box

        def build_updates_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(page, "set_margin_" + m)(12)
            self.summary = ui.label("Looking for updates…", "lx-title", wrap=True)
            page.append(self.summary)
            self.age = ui.label("", "lx-dim lx-small")
            page.append(self.age)
            self.removals = []
            self.removal_note = ui.label("", "lx-banner warning", wrap=True)
            self.removal_note.set_visible(False)
            page.append(self.removal_note)
            actions = Gtk.Box(spacing=6)
            self.install_all_btn = Gtk.Button(label="Install all")
            self.install_all_btn.add_css_class("suggested-action")
            self.install_all_btn.connect("clicked", lambda *_: self.install_debian())
            self.security_btn = Gtk.Button(label="Install security updates only")
            self.security_btn.connect("clicked", lambda *_: self.run_root(["security-upgrade"], "Security updates"))
            self.download_btn = Gtk.Button(label="Download only")
            ui.accessible_label(self.download_btn, "Download Debian updates now and install them later")
            self.download_btn.connect("clicked", lambda *_: self.run_root(["download-upgrades"], "Downloading"))
            for b in (self.install_all_btn, self.security_btn, self.download_btn):
                actions.append(b)
            page.append(actions)
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class("boxed-list")
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(self.list)
            page.append(sw)
            self.stack.add_titled(page, "updates", "Updates")

        def build_history_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(page, "set_margin_" + m)(12)
            page.append(ui.label("Package history (apt)", "lx-heading"))
            self.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.history.add_css_class("boxed-list")
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(self.history)
            page.append(sw)
            row = Gtk.Box(spacing=6)
            for title, path in (("LuxenOS package log", updates.PKG_LOG),
                                ("Automatic update log", updates.UPDATES_LOG)):
                b = Gtk.Button(label=title)
                b.connect("clicked", lambda _b, p=path, t=title: self.show_log(t, p))
                row.append(b)
            page.append(row)
            self.stack.add_titled(page, "history", "History")

        def build_settings_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(page, "set_margin_" + m)(12)
            page.append(ui.label("Automatic updates", "lx-heading"))
            page.append(ui.label("Stored in /etc/luxenos/updater.conf (also `sudo luxenos-update-settings`). "
                                 "Saving asks for an administrator password.", "lx-dim lx-small", wrap=True))
            grid = Gtk.Grid(column_spacing=12, row_spacing=6)
            self.setting_widgets = {}
            for i, (key, label, kind, _d) in enumerate(updates.SCHEDULE_SETTINGS):
                lbl = ui.label(label)
                lbl.set_hexpand(True)
                grid.attach(lbl, 0, i, 1, 1)
                if kind == "bool":
                    w = Gtk.Switch(halign=Gtk.Align.END)
                else:
                    w = Gtk.Entry(max_length=5, width_chars=6, halign=Gtk.Align.END)
                ui.accessible_label(w, label)
                grid.attach(w, 1, i, 1, 1)
                self.setting_widgets[key] = (kind, w)
            page.append(grid)
            save = Gtk.Button(label="Save settings", halign=Gtk.Align.START)
            save.add_css_class("suggested-action")
            save.connect("clicked", lambda *_: self.save_settings())
            page.append(save)
            self.load_settings()
            sw = Gtk.ScrolledWindow()
            sw.set_child(page)
            self.stack.add_titled(sw, "settings", "Settings")

        # -- scanning --
        def scan(self):
            self.summary.set_text("Looking for updates…")
            self.age.set_text("Package lists downloaded %s" % updates.age_text(updates.lists_age()))
            self.set_actions(False)
            threading.Thread(target=self.scan_worker, daemon=True).start()
            self.load_history()

        def scan_worker(self):
            found, errors = {}, {}
            ups, _new, removed, errs = updates.apt_upgrades()
            found["security"] = [u for u in ups if u.security and u.source == "debian"]
            found["debian"] = [u for u in ups if not u.security and u.source == "debian"]
            found["android"] = [u for u in ups if u.source == "android"]
            if errs:
                errors["debian"] = "; ".join(errs)
            self.removals = removed
            found["flatpak"], e = updates.flatpak_updates()
            if e:
                errors["flatpak"] = e
            found["firmware"], e = updates.firmware_updates()
            if e:
                errors["firmware"] = e
            GLib.idle_add(self.show_scan, found, errors)

        def show_scan(self, found, errors):
            self.found, self.errors = found, errors
            self.list.remove_all()
            total = sum(len(v) for v in found.values())
            n_deb = len(found["security"]) + len(found["debian"]) + len(found["android"])
            if total:
                self.summary.set_text("%d update%s available" % (total, "" if total == 1 else "s"))
            elif errors.get("debian"):
                self.summary.set_text("Could not check Debian updates")
            else:
                self.summary.set_text("Everything is up to date")
            for key in ("security", "debian", "android", "flatpak", "firmware"):
                items = found.get(key, [])
                err = errors.get(key)
                if not items and not err:
                    continue
                self.list.append(self.make_section(key, items, err))
            self.removal_note.set_visible(bool(self.removals))
            self.removal_note.set_text("These updates would remove: %s. You will be asked first."
                                       % ", ".join(self.removals[:8]))
            self.set_actions(not self.busy)
            self.install_all_btn.set_sensitive(not self.busy and n_deb > 0)
            self.security_btn.set_sensitive(not self.busy and bool(found["security"]))
            self.download_btn.set_sensitive(not self.busy and n_deb > 0)
            return False

        def make_section(self, key, items, err):
            title, icon = SOURCE_TITLES[key]
            row = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(8)
            head = Gtk.Box(spacing=8)
            head.append(Gtk.Image.new_from_icon_name(icon))
            h = ui.label("%s (%d)" % (title, len(items)), "lx-heading")
            h.set_hexpand(True)
            head.append(h)
            if key == "flatpak" and items:
                b = Gtk.Button(label="Update")
                b.connect("clicked", lambda *_: self.update_flatpaks())
                head.append(b)
            elif key == "firmware" and items:
                b = Gtk.Button(label="Update firmware…")
                b.connect("clicked", lambda *_: self.confirm_firmware())
                head.append(b)
            elif key == "android" and items:
                b = Gtk.Button(label="Update Android…")
                b.connect("clicked", lambda *_: self.confirm_android())
                head.append(b)
            box.append(head)
            if err:
                box.append(ui.label(err, "lx-warn lx-small", wrap=True))
            for u in items[:200]:
                text = u.name
                if u.old or u.new:
                    text += "   %s → %s" % (u.old or "?", u.new or "?")
                lbl = ui.label(text, "lx-small", selectable=True)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                if u.detail:
                    lbl.set_tooltip_text(u.detail)
                box.append(lbl)
            if len(items) > 200:
                box.append(ui.label("… and %d more" % (len(items) - 200), "lx-dim lx-small"))
            row.set_child(box)
            return row

        def load_history(self):
            self.history.remove_all()
            entries = updates.apt_history()
            if not entries:
                self.history.append(ui.label("No package history yet.", "lx-dim"))
            for e in entries:
                parts = [("%d upgraded" % e["upgraded"]) if e["upgraded"] else "",
                         ("%d installed" % e["installed"]) if e["installed"] else "",
                         ("%d removed" % e["removed"]) if e["removed"] else ""]
                text = "%s — %s" % (e["date"], ", ".join(p for p in parts if p) or "no changes")
                vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                for m in ("start", "end", "top", "bottom"):
                    getattr(vb, "set_margin_" + m)(4)
                vb.append(ui.label(text, "lx-heading" if not e["error"] else "lx-crit"))
                cmd = ui.label(e["command"] or "", "lx-dim lx-small")
                cmd.set_ellipsize(Pango.EllipsizeMode.END)
                vb.append(cmd)
                if e["error"]:
                    vb.append(ui.label("Error: " + e["error"], "lx-crit lx-small", wrap=True))
                self.history.append(vb)

        def show_log(self, title, path):
            buf = self.output.get_buffer()
            buf.set_text("\n".join(updates.log_tail(path)))
            self.status.set_text("%s: %s" % (title, path))

        def load_settings(self):
            cur = updates.schedule()
            self.current_settings = cur
            for key, (kind, w) in self.setting_widgets.items():
                if kind == "bool":
                    w.set_active(cur.get(key) == "yes")
                else:
                    w.set_text(cur.get(key, ""))

        def save_settings(self):
            wanted = {}
            for key, (kind, w) in self.setting_widgets.items():
                wanted[key] = ("yes" if w.get_active() else "no") if kind == "bool" else w.get_text().strip()
            try:
                changes = updates.schedule_changes(self.current_settings, wanted)
            except ValueError as e:
                ui.alert(self, "Invalid setting", str(e))
                return
            if not changes:
                self.status.set_text("No settings changed.")
                return
            self.run_root(["set-update-schedule"] + changes, "Saving settings", after=self.load_settings)

        # -- actions --
        def set_actions(self, on):
            for b in (self.install_all_btn, self.security_btn, self.download_btn, self.check_btn):
                b.set_sensitive(on)

        def check_online(self):
            self.run_root(["update-lists"], "Downloading package lists", after=self.scan)

        def install_debian(self):
            if self.removals:
                ui.alert(self, "Remove packages?", "Installing all updates would also remove:\n\n%s\n\nA restore "
                         "point is created first." % ", ".join(self.removals), buttons=("Cancel", "Update"),
                         default=0, cancel=0,
                         callback=lambda i: i == 1 and self.run_root(["upgrade"], "Installing updates"))
            else:
                self.run_root(["upgrade"], "Installing updates")

        def update_flatpaks(self):
            user = [u for u in self.found.get("flatpak", []) if "(user)" in u.detail]
            system = [u for u in self.found.get("flatpak", []) if "(system)" in u.detail]

            def then_system(_rc=0):
                if system:
                    self.run_root(["flatpak-update"], "Updating Flatpak applications")
                else:
                    self.scan()

            if user:
                self.run_cmd(["flatpak", "update", "--user", "-y", "--noninteractive"],
                             "Updating your Flatpak applications", after=then_system)
            else:
                then_system()

        def confirm_firmware(self):
            ui.alert(self, "Update firmware?",
                     "Firmware updates change the software inside your hardware. Keep the computer plugged in and "
                     "do not switch it off. Some updates are installed during the next restart (the computer is "
                     "never restarted automatically).", buttons=("Cancel", "Update firmware"), default=0, cancel=0,
                     callback=lambda i: i == 1 and self.run_root(["firmware-update"], "Updating firmware"))

        def confirm_android(self):
            ui.alert(self, "Update Android?",
                     "Running Android apps are closed while Waydroid and its Android image are updated.",
                     buttons=("Cancel", "Update"), default=0, cancel=0,
                     callback=lambda i: i == 1 and self.run_root(["waydroid-upgrade"], "Updating Android"))

        def run_root(self, args, title, after=None):
            if not os.access(pkg.ROOT_HELPER, os.X_OK):
                ui.alert(self, "Updates are unavailable", "%s is missing." % pkg.ROOT_HELPER)
                return
            self.run_cmd(["pkexec", pkg.ROOT_HELPER] + list(args), title, after,
                         auth_hint=" (you will be asked for an administrator password)")

        def run_cmd(self, argv, title, after=None, auth_hint=""):
            if self.busy:
                ui.alert(self, "Please wait", "Another update operation is still running.")
                return
            self.busy = True
            self.set_actions(False)
            self.log_lines = []
            self.progress.set_visible(True)
            self.progress.set_fraction(0)
            self.status.remove_css_class("lx-crit")
            self.status.set_text(title + "…" + auth_hint)
            buf = self.output.get_buffer()
            buf.set_text("")

            def on_line(line):
                kind, pct, text = pkg.parse_status_line(line)
                if kind in ("install", "download") and pct is not None:
                    self.progress.set_fraction(max(0.0, min(1.0, pct / 100.0)))
                    self.status.set_text(text)
                elif kind == "status":
                    self.status.set_text(text)
                    self.progress.pulse()
                elif kind == "log":
                    self.log_lines.append(text)
                if kind != "status":
                    buf.insert(buf.get_end_iter(), text + "\n")

            def on_done(rc):
                self.busy = False
                self.progress.set_visible(False)
                if rc in (126, 127) and not self.log_lines:
                    self.status.set_text("Authentication was cancelled; nothing was changed.")
                elif rc != 0:
                    advice, repair = pkg.diagnose(self.log_lines)
                    self.status.set_text("%s failed. %s" % (title, advice))
                    self.status.add_css_class("lx-crit")
                else:
                    self.status.set_text("%s: done." % title)
                if after is not None and rc == 0:
                    after()
                else:
                    self.scan()

            ui.run_streaming(argv, on_line, on_done)

        def on_close(self, *_):
            if self.busy:
                ui.alert(self, "Please wait", "An update is still running. Closing now could leave the system "
                                              "half-updated.")
                return True
            return False

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv[:1])
