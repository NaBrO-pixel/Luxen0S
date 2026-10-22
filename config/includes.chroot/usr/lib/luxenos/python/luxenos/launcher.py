"""Spotlight-style launcher (Super+Space).

Searches installed applications (name, generic name, keywords, description),
quick actions, recent documents and files, and launches applications through
their desktop entries (Gio honours Exec, Terminal, Path and the launch
environment). It stays resident after the first start (started hidden by
luxenos-session-init), so opening it is instant; running `luxenos-launcher`
again toggles it.

Keyboard: type to search, Up/Down (or Tab) to move, Enter to open, Escape to
close. Files are opened with their default application; executables and
scripts are never run from here, their folder is opened instead.
"""

import os
import shutil
import sys
import threading

from . import apps as appsmod
from . import filesearch, ui

APP_ID = "org.luxenos.Launcher"
WIDTH = 640
MAX_HEIGHT = 440

# (label, icon, argv, needs confirmation, extra search words)
QUICK_ACTIONS = (
    ("Settings", "preferences-system-symbolic", ["luxenos-settings"], False, "preferences control panel"),
    ("Quick Settings", "emblem-system-symbolic", ["luxenos-quick-settings"], False, "wifi bluetooth volume brightness"),
    ("Workspace Overview", "view-grid-symbolic", ["luxenos-overview"], False, "windows expose mission"),
    ("Lock Screen", "system-lock-screen-symbolic", ["luxenos-lock"], False, "lock"),
    ("Log Out", "system-log-out-symbolic", ["swaymsg", "exit"], True, "logout sign out exit"),
    ("Restart", "system-reboot-symbolic", ["systemctl", "reboot"], True, "reboot"),
    ("Shut Down", "system-shutdown-symbolic", ["systemctl", "poweroff"], True, "power off poweroff halt"),
)


class Result:
    __slots__ = ("kind", "title", "subtitle", "icon", "gicon", "payload", "confirm")

    def __init__(self, kind, title, subtitle="", icon=None, gicon=None, payload=None, confirm=False):
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.gicon = gicon
        self.payload = payload
        self.confirm = confirm


def available_actions():
    return [a for a in QUICK_ACTIONS if shutil.which(a[2][0])]


def match_actions(query, actions):
    q = appsmod.normalize(query)
    out = []
    for label, icon, argv, confirm, words in actions:
        hay = appsmod.normalize(label + " " + words)
        if not q or all(t in hay for t in q.split()):
            out.append(Result("action", label, "Quick action", icon=icon, payload=argv, confirm=confirm))
    return out


def main(argv=None):
    Gtk = ui.init(layer_shell=True)
    from gi.repository import Gdk, Gio, GLib, Pango

    class LauncherWindow(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Launcher")
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            self.set_resizable(False)
            ui.make_layer(self, "luxenos-launcher", layer="overlay", anchors=("top",),
                          margins={"top": 120}, keyboard="exclusive", exclusive_zone=-1)

            self.apps = []
            self.index_dirty = True
            self.usage = appsmod.load_usage()
            self.files = filesearch.FileSearcher()
            self.generation = 0
            self.pending_confirm = None
            self.results = []
            self.file_results = []

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.add_css_class("lx-glass")
            box.set_size_request(WIDTH, -1)
            self.set_child(box)

            self.entry = Gtk.SearchEntry()
            self.entry.add_css_class("lx-search")
            self.entry.set_placeholder_text("Search apps, files and actions")
            self.entry.set_hexpand(True)
            ui.accessible_label(self.entry, "Search applications, files and actions")
            self.entry.connect("search-changed", lambda *_: self.refresh())
            self.entry.connect("activate", lambda *_: self.activate_selected())
            self.entry.connect("stop-search", lambda *_: self.close_launcher())
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_key)
            self.entry.add_controller(keys)
            box.append(self.entry)

            self.listbox = Gtk.ListBox()
            self.listbox.add_css_class("lx-results")
            self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
            self.listbox.set_header_func(self.header_func)
            self.listbox.connect("row-activated", lambda _l, row: self.activate(row))
            ui.accessible_label(self.listbox, "Search results")
            self.scroller = Gtk.ScrolledWindow()
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroller.set_propagate_natural_height(True)
            self.scroller.set_max_content_height(MAX_HEIGHT)
            self.scroller.set_child(self.listbox)
            box.append(self.scroller)

            self.footer = ui.label("", "lx-dim lx-small")
            box.append(self.footer)

            ui.add_escape_handler(self, self.close_launcher)
            if not ui.LAYER_SHELL:
                # Without layer-shell: a normal window that closes when it
                # loses focus, like a popup (Sway floats it, see sway config).
                self.connect("notify::is-active", self.on_active_changed)

            monitor = Gio.AppInfoMonitor.get()
            monitor.connect("changed", lambda *_: setattr(self, "index_dirty", True))

        # -- data --
        def ensure_apps(self):
            if self.index_dirty:
                self.apps = appsmod.load_apps()
                self.index_dirty = False

        def header_func(self, row, before):
            if before is None or before.result.kind != row.result.kind:
                titles = {"app": "Applications", "action": "Quick actions",
                          "recent": "Recent documents", "file": "Files", "info": ""}
                text = titles.get(row.result.kind, "")
                row.set_header(ui.label(text, "lx-section") if text else None)
            else:
                row.set_header(None)

        def make_row(self, result):
            row = Gtk.ListBoxRow()
            row.result = result
            hb = Gtk.Box(spacing=12)
            if result.gicon is not None:
                img = ui.gicon_image(result.gicon, 32)
            else:
                img = Gtk.Image.new_from_icon_name(result.icon or "application-x-executable")
                img.set_pixel_size(32)
            hb.append(img)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            vb.set_valign(Gtk.Align.CENTER)
            t = ui.label(result.title, "lx-heading")
            t.set_ellipsize(Pango.EllipsizeMode.END)
            vb.append(t)
            if result.subtitle:
                s = ui.label(result.subtitle, "lx-dim lx-small")
                s.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                vb.append(s)
                row.subtitle_label = s
            hb.append(vb)
            row.set_child(hb)
            ui.accessible_label(row, "%s. %s" % (result.title, result.subtitle))
            return row

        def refresh(self):
            self.generation += 1
            self.pending_confirm = None
            query = self.entry.get_text().strip()
            self.ensure_apps()
            results = []
            if query:
                for e in appsmod.rank(query, self.apps, self.usage, limit=8):
                    results.append(Result("app", e.name, e.description or e.generic,
                                          gicon=e.info.get_icon(), payload=e))
                results += match_actions(query, available_actions())
                results += self.recent_items(query, 4)
            else:
                frequent = sorted((e for e in self.apps if self.usage.get(e.app_id)),
                                  key=lambda e: -self.usage[e.app_id])[:6]
                for e in frequent:
                    results.append(Result("app", e.name, e.description or e.generic,
                                          gicon=e.info.get_icon(), payload=e))
                results += match_actions("", available_actions())
                results += self.recent_items("", 5)
            self.results = results
            self.file_results = []
            self.rebuild()
            if len(query) >= 2:
                self.footer.set_text("Searching files (%s)..." % self.files.backend)
                gen = self.generation
                threading.Thread(target=self.search_files, args=(query, gen), daemon=True).start()
            else:
                self.footer.set_text("Enter to open · ↑↓ to move · Esc to close")

        def rebuild(self):
            self.listbox.remove_all()
            items = self.results + self.file_results
            if not items:
                items = [Result("info", "No results", "Try another word", icon="edit-find-symbolic")]
            for r in items:
                self.listbox.append(self.make_row(r))
            first = self.listbox.get_row_at_index(0)
            if first is not None and first.result.kind != "info":
                self.listbox.select_row(first)

        def recent_items(self, query, limit):
            out = []
            q = query.lower()
            try:
                items = Gtk.RecentManager.get_default().get_items()
            except GLib.Error:
                return out
            items.sort(key=lambda i: i.get_modified().to_unix() if i.get_modified() else 0, reverse=True)
            for item in items:
                if not item.is_local() or not item.exists():
                    continue
                name = item.get_display_name() or ""
                if q and q not in name.lower():
                    continue
                path = GLib.filename_from_uri(item.get_uri())[0]
                out.append(Result("recent", name, path, gicon=item.get_gicon(), payload=path))
                if len(out) >= limit:
                    break
            return out

        def search_files(self, query, gen):
            try:
                paths = self.files.search(query, limit=8)
            except Exception as e:  # never let a worker crash the launcher
                print("luxenos-launcher: file search failed: %s" % e, file=sys.stderr)
                paths = []
            GLib.idle_add(self.show_files, paths, gen)

        def show_files(self, paths, gen):
            if gen != self.generation:
                return False
            shown = {r.payload for r in self.results if r.kind == "recent"}
            self.file_results = []
            for p in paths:
                if p in shown:
                    continue
                f = Gio.File.new_for_path(p)
                gicon = None
                try:
                    fi = f.query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
                    gicon = fi.get_icon()
                except GLib.Error:
                    pass
                self.file_results.append(Result("file", os.path.basename(p), os.path.dirname(p),
                                                gicon=gicon, payload=p))
            selected = self.listbox.get_selected_row()
            index = selected.get_index() if selected else 0
            self.rebuild()
            row = self.listbox.get_row_at_index(index)
            if row is not None and row.result.kind != "info":
                self.listbox.select_row(row)
            self.footer.set_text("Enter to open · ↑↓ to move · Esc to close")
            return False

        # -- keyboard --
        def on_key(self, _ctl, keyval, _code, state):
            if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
                self.move(1)
                return True
            if keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
                self.move(-1)
                return True
            if keyval in (Gdk.KEY_Page_Down,):
                self.move(5)
                return True
            if keyval in (Gdk.KEY_Page_Up,):
                self.move(-5)
                return True
            return False

        def move(self, delta):
            row = self.listbox.get_selected_row()
            index = (row.get_index() if row else -1) + delta
            count = len(self.results) + len(self.file_results)
            if count == 0:
                return
            index = max(0, min(count - 1, index))
            target = self.listbox.get_row_at_index(index)
            if target is not None:
                self.listbox.select_row(target)
                adj = self.scroller.get_vadjustment()
                ok, rect = target.compute_bounds(self.listbox)
                if ok:
                    y, h = rect.get_y(), rect.get_height()
                    if y < adj.get_value():
                        adj.set_value(y)
                    elif y + h > adj.get_value() + adj.get_page_size():
                        adj.set_value(y + h - adj.get_page_size())

        def activate_selected(self):
            row = self.listbox.get_selected_row()
            if row is not None:
                self.activate(row)

        def activate(self, row):
            r = row.result
            if r.kind == "info":
                return
            if r.confirm and self.pending_confirm is not row:
                self.pending_confirm = row
                if hasattr(row, "subtitle_label"):
                    row.subtitle_label.set_text("Press Enter again to confirm")
                    row.subtitle_label.add_css_class("lx-warn")
                return
            ok = True
            if r.kind == "app":
                ok = self.launch_app(r.payload)
            elif r.kind == "action":
                ok = ui.spawn(r.payload)
            elif r.kind in ("file", "recent"):
                ok = self.open_path(r.payload)
            if ok:
                self.close_launcher()

        def launch_app(self, entry):
            ctx = Gdk.Display.get_default().get_app_launch_context()
            try:
                entry.info.launch([], ctx)
            except GLib.Error as e:
                self.footer.set_text("Could not start %s: %s" % (entry.name, e.message))
                self.footer.add_css_class("lx-crit")
                return False
            self.usage = appsmod.record_launch(entry.app_id)
            return True

        def open_path(self, path):
            f = Gio.File.new_for_path(path)
            target = f
            try:
                info = f.query_info("standard::content-type,access::can-execute",
                                    Gio.FileQueryInfoFlags.NONE, None)
                ctype = info.get_content_type() or ""
                executable = info.get_attribute_boolean("access::can-execute") and \
                    info.get_file_type() != Gio.FileType.DIRECTORY
                if (executable and Gio.content_type_can_be_executable(ctype)) or \
                        ctype in ("application/x-desktop", "application/x-shellscript"):
                    # Never run programs from search results: show them instead.
                    target = f.get_parent()
            except GLib.Error:
                pass
            ctx = Gdk.Display.get_default().get_app_launch_context()
            try:
                Gio.AppInfo.launch_default_for_uri(target.get_uri(), ctx)
            except GLib.Error as e:
                self.footer.set_text("Cannot open: %s" % e.message)
                return False
            return True

        # -- visibility --
        def open_launcher(self):
            self.entry.set_text("")
            self.footer.remove_css_class("lx-crit")
            self.usage = appsmod.load_usage()
            self.refresh()
            self.present()
            self.entry.grab_focus()

        def close_launcher(self):
            self.generation += 1
            self.set_visible(False)

        def on_active_changed(self, *_):
            if not self.is_active() and self.get_visible():
                self.close_launcher()

    class LauncherApp(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window = None
            self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                                 "Start hidden and stay resident", None)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()
            self.theme_monitor = ui.watch_theme()

        def do_command_line(self, cmdline):
            opts = cmdline.get_options_dict().end().unpack()
            if self.window is None:
                self.window = LauncherWindow(self)
                self.hold()  # stay resident: the next Super+Space is instant
            if opts.get("background"):
                return 0
            if self.window.get_visible():
                self.window.close_launcher()
            else:
                self.window.open_launcher()
            return 0

    return LauncherApp().run(argv if argv is not None else sys.argv)
