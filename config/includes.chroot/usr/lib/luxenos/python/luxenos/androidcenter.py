"""Android Integration Center (Waydroid).

Start, stop and restart Android; set it up the first time with progress;
install APKs from a file; launch, remove and pin Android apps, open their
Android permission page; choose the display size; see storage use; and a
troubleshooting page that explains what is wrong in plain words. Uses
luxenos.android (user-level `waydroid` commands) and, for the container
service and the image download only, luxenos-android-root through pkexec.

Waydroid itself, the first-boot service and `luxenos-setup-android` stay as
they were; this is a graphical front end to them.
"""

import os
import sys
import threading

from . import android, dock, ui

APP_ID = "org.luxenos.Android"
ICONS = {"ok": "emblem-ok-symbolic", "warning": "dialog-warning-symbolic", "problem": "dialog-error-symbolic"}


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gio, GLib, Pango

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Android")
            self.set_default_size(960, 660)
            self.busy = False
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.stack = Gtk.Stack()
            self.stack.set_transition_duration(ui.transition_ms(150))
            header.set_title_widget(Gtk.StackSwitcher(stack=self.stack))
            header.pack_start(ui.icon_button("view-refresh-symbolic", "Refresh", self.refresh))
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            outer.append(self.stack)
            self.status = ui.label("", "lx-small", wrap=True)
            self.output = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            exp = Gtk.Expander(label="Details")
            sw = Gtk.ScrolledWindow(min_content_height=110)
            sw.set_child(self.output)
            exp.set_child(sw)
            for w in (self.status, exp):
                for m in ("start", "end", "bottom"):
                    getattr(w, "set_margin_" + m)(10)
                outer.append(w)
            self.set_child(outer)
            if not android.available():
                self.stack.add_titled(ui.label("Waydroid is not installed on this system.", "lx-title"),
                                      "none", "Android")
                return
            self.build_overview()
            self.build_apps()
            self.build_display()
            self.build_storage()
            self.build_trouble()
            self.refresh()

        def page(self, name, title):
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(16)
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(vb)
            self.stack.add_titled(sw, name, title)
            return vb

        def button(self, box, label, cb, css=None):
            b = Gtk.Button(label=label)
            if css:
                b.add_css_class(css)
            b.connect("clicked", lambda *_: cb())
            box.append(b)
            return b

        # -- pages --
        def build_overview(self):
            vb = self.page("overview", "Overview")
            self.state_title = ui.label("…", "lx-title")
            vb.append(self.state_title)
            self.state_detail = ui.label("", "lx-dim", wrap=True)
            vb.append(self.state_detail)
            row = Gtk.Box(spacing=6)
            self.start_btn = self.button(row, "Start Android", self.start, "suggested-action")
            self.stop_btn = self.button(row, "Stop Android", self.stop)
            self.button(row, "Restart Android", self.restart)
            self.button(row, "Show Android", lambda: ui.spawn(["waydroid", "show-full-ui"]))
            vb.append(row)
            vb.append(ui.label("Set up", "lx-heading"))
            setup = Gtk.Box(spacing=6)
            self.image_type = Gtk.DropDown.new_from_strings(["With Google services (GAPPS)",
                                                             "Without Google services (VANILLA)"])
            ui.accessible_label(self.image_type, "Android image type")
            setup.append(self.image_type)
            self.setup_btn = self.button(setup, "Set up Android", lambda: self.root(["init", self.kind()],
                                                                                    "Setting up Android"))
            self.button(setup, "Download the Android image again…", self.confirm_reinit)
            vb.append(setup)
            vb.append(ui.label("Setting up downloads about 1 GB and takes several minutes. Your apps and their data "
                               "live in your home folder and are kept when the image is downloaded again.",
                               "lx-dim lx-small", wrap=True))

        def kind(self):
            return ("GAPPS", "VANILLA")[self.image_type.get_selected()]

        def build_apps(self):
            vb = self.page("apps", "Apps")
            row = Gtk.Box(spacing=6)
            self.button(row, "Install APK file…", self.pick_apk, "suggested-action")
            if os.path.exists(android.APK_BUNDLED):
                self.button(row, "Install Aurora Store", lambda: self.install_apk(android.APK_BUNDLED))
            vb.append(row)
            vb.append(ui.label("Only install apps from sources you trust. Android permissions are managed inside "
                               "Android: “Permissions” opens the app's page there.", "lx-dim lx-small", wrap=True))
            self.apps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.apps.add_css_class("boxed-list")
            vb.append(self.apps)

        def build_display(self):
            vb = self.page("display", "Display")
            self.res = Gtk.DropDown.new_from_strings([r[0] for r in android.RESOLUTIONS])
            ui.accessible_label(self.res, "Android display size")
            vb.append(ui.label("Display size", "lx-heading"))
            vb.append(self.res)
            self.multi = Gtk.CheckButton(label="Open each Android app in its own window")
            vb.append(self.multi)
            apply = Gtk.Button(label="Apply (restarts Android)", halign=Gtk.Align.START)
            apply.connect("clicked", lambda *_: self.apply_display())
            vb.append(apply)
            vb.append(ui.label("Android has to be running to change these settings.", "lx-dim lx-small"))

        def build_storage(self):
            vb = self.page("storage", "Storage")
            self.storage_label = ui.label("…", None, wrap=True, selectable=True)
            vb.append(self.storage_label)
            vb.append(ui.label("Removing Android apps (Apps page) frees their space. The Android system image is "
                               "shared by every user of this computer.", "lx-dim lx-small", wrap=True))

        def build_trouble(self):
            vb = self.page("trouble", "Troubleshooting")
            self.checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            vb.append(self.checks_box)
            row = Gtk.Box(spacing=6)
            self.button(row, "Restart Android services", lambda: self.root(["container-restart"], "Restarting"))
            self.button(row, "Retry first-boot setup", lambda: self.root(["retry-first-boot"], "Retrying"))
            self.button(row, "Show logs", lambda: self.root(["log"], "Reading logs"))
            vb.append(row)

        # -- state --
        def refresh(self):
            def work():
                st = android.status()
                apps = []
                if st.get("session") == "RUNNING":
                    rc, out = android.run(["app", "list"], timeout=30)
                    apps = android.parse_app_list(out) if rc == 0 else []
                checks = android.collect_checks()
                store = android.storage()
                GLib.idle_add(self.show, st, apps, checks, store)
            threading.Thread(target=work, daemon=True).start()

        def show(self, st, apps, checks, store):
            running = st.get("session") == "RUNNING"
            if not st.get("initialized"):
                self.state_title.set_text("Android is not set up yet")
                self.state_detail.set_text("Choose an image type below and press “Set up Android”.")
            else:
                self.state_title.set_text("Android is running" if running else "Android is stopped")
                self.state_detail.set_text("Container: %s · %s%s" % (
                    st.get("container", "?").lower(), st.get("vendor", ""),
                    " · IP %s" % st["ip"] if st.get("ip") else ""))
            self.start_btn.set_sensitive(st.get("initialized") and not running and not self.busy)
            self.stop_btn.set_sensitive(running and not self.busy)
            self.setup_btn.set_sensitive(not st.get("initialized") and not self.busy)
            self.apps.remove_all()
            if not running:
                self.apps.append(ui.label("Start Android to see and install apps.", "lx-dim"))
            elif not apps:
                self.apps.append(ui.label("No Android apps found.", "lx-dim"))
            for a in apps:
                self.apps.append(self.app_row(a))
            while (c := self.checks_box.get_first_child()) is not None:
                self.checks_box.remove(c)
            for level, title, advice in checks:
                r = Gtk.Box(spacing=10)
                r.add_css_class("lx-card")
                r.append(Gtk.Image.new_from_icon_name(ICONS[level]))
                tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                tb.append(ui.label(title, "lx-heading", wrap=True))
                if advice:
                    tb.append(ui.label(advice, "lx-dim lx-small", wrap=True))
                r.append(tb)
                self.checks_box.append(r)
            data = ui.human_bytes(store["data"]) if store["data"] is not None else "not readable"
            self.storage_label.set_text("Android system image: %s\nYour Android apps and data (%s): %s\n"
                                        "Free disk space: %s" % (ui.human_bytes(store["images"]), store["data_dir"],
                                                                 data, ui.human_bytes(store["free"])))
            return False

        def app_row(self, a):
            row = Gtk.Box(spacing=6)
            for m in ("start", "end", "top", "bottom"):
                getattr(row, "set_margin_" + m)(6)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            t = ui.label(a["name"], "lx-heading")
            t.set_ellipsize(Pango.EllipsizeMode.END)
            vb.append(t)
            vb.append(ui.label(a["package"], "lx-dim lx-small"))
            row.append(vb)
            pkgname = a["package"]
            self.button(row, "Open", lambda: self.user(["app", "launch", pkgname], "Opening %s" % a["name"]))
            self.button(row, "Permissions", lambda: self.user(
                ["app", "intent", "android.settings.APPLICATION_DETAILS_SETTINGS", "package:" + pkgname],
                "Opening Android settings"))
            sc = "waydroid.%s.desktop" % pkgname

            def shortcut():
                if not android.waydroid_entry_exists(pkgname):
                    android.create_shortcut(a["name"], pkgname)
                    target = "luxenos-android-%s.desktop" % pkgname
                else:
                    target = sc
                try:
                    dock.set_pinned(target, True)
                    self.status.set_text("%s is in the dock and the launcher." % a["name"])
                except (ValueError, OSError) as e:
                    self.status.set_text("Could not pin: %s" % e)
            self.button(row, "Pin to dock", shortcut)
            self.button(row, "Remove…", lambda: ui.alert(
                self, "Remove %s?" % a["name"], "The app and its data inside Android are deleted.",
                buttons=("Cancel", "Remove"), default=0, cancel=0,
                callback=lambda i: i == 1 and self.user(["app", "remove", pkgname], "Removing %s" % a["name"])))
            return row

        # -- actions --
        def start(self):
            android.start_session()
            self.status.set_text("Starting Android…")
            GLib.timeout_add_seconds(6, lambda: self.refresh() or False)

        def stop(self):
            self.user(["session", "stop"], "Stopping Android")

        def restart(self):
            def then():
                self.start()
            self.user(["session", "stop"], "Restarting Android", after=then)

        def confirm_reinit(self):
            ui.alert(self, "Download the Android image again?", "This replaces the Android system (about 1 GB). Your "
                     "apps and their data are kept. Use it when Android no longer starts.",
                     buttons=("Cancel", "Download"), default=0, cancel=0,
                     callback=lambda i: i == 1 and self.root(["reinit", self.kind()], "Downloading Android"))

        def pick_apk(self):
            dialog = Gtk.FileDialog(title="Install an Android app")
            filt = Gtk.FileFilter()
            filt.set_name("Android apps (APK)")
            filt.add_pattern("*.apk")
            filt.add_pattern("*.APK")
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dialog.set_filters(store)

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is not None and f.get_path():
                    self.install_apk(f.get_path())

            dialog.open(self, None, done)

        def install_apk(self, path):
            err = android.valid_apk(path)
            if err:
                ui.alert(self, "This file cannot be installed", err)
                return
            if android.status().get("session") != "RUNNING":
                ui.alert(self, "Start Android first", "Apps can only be installed while Android is running.")
                return
            self.user(["app", "install", path], "Installing %s" % os.path.basename(path), timeout=300)

        def apply_display(self):
            _label, w, h = android.RESOLUTIONS[self.res.get_selected()]
            err = android.set_resolution(w, h)
            if err is None:
                rc, out = android.run(["prop", "set", "persist.waydroid.multi_windows",
                                       "true" if self.multi.get_active() else "false"], timeout=20)
                err = None if rc == 0 else out
            if err:
                ui.alert(self, "Display settings not changed", android.explain(err) or err)
                return
            self.restart()

        # -- running commands --
        def log(self, text):
            buf = self.output.get_buffer()
            buf.insert(buf.get_end_iter(), text + "\n")

        def user(self, args, title, after=None, timeout=60):
            if self.busy:
                return
            self.busy = True
            self.status.set_text(title + "…")

            def work():
                rc, out = android.run(args, timeout=timeout)
                GLib.idle_add(done, rc, out)

            def done(rc, out):
                self.busy = False
                if out:
                    self.log(out)
                if rc == 0:
                    self.status.set_text(title + ": done.")
                    if after:
                        after()
                else:
                    self.status.set_text("%s failed. %s" % (title, android.explain(out) or "See Details."))
                GLib.timeout_add_seconds(2, lambda: self.refresh() or False)
                return False

            threading.Thread(target=work, daemon=True).start()

        def root(self, args, title):
            if self.busy:
                return
            if not os.access(android.ROOT_HELPER, os.X_OK):
                ui.alert(self, "Unavailable", "%s is missing." % android.ROOT_HELPER)
                return
            self.busy = True
            self.status.set_text(title + "… (you will be asked for an administrator password)")
            self.output.get_buffer().set_text("")
            lines = []

            def on_line(line):
                if line.startswith("@@STATUS "):
                    self.status.set_text(line[9:])
                else:
                    lines.append(line[6:] if line.startswith("@@LOG ") else line)
                    self.log(lines[-1])

            def on_done(rc):
                self.busy = False
                if rc in (126, 127) and not lines:
                    self.status.set_text("Authentication was cancelled; nothing was changed.")
                elif rc != 0:
                    self.status.set_text("%s failed. %s" % (title, android.explain("\n".join(lines)) or
                                                            (lines[-1] if lines else "")))
                self.refresh()

            ui.run_streaming(["pkexec", android.ROOT_HELPER] + list(args), on_line, on_done)

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv if argv is not None else sys.argv[:1])
