"""LuxenOS Software Center.

Browse and search the Debian catalogue, see what is installed and which
updates are waiting, and install, update or remove software. It is a front
end for apt, not a package manager of its own:

  * reading: python3-apt, unprivileged (luxenos.pkg.load_catalogue)
  * every change: shown first as apt's own simulation (what else would be
    installed or removed), then confirmed by the user, then run by
    luxenos-pkg-root through pkexec (administrator password). apt verifies
    repository signatures and holds the dpkg lock; the helper adds its own
    lock, so two LuxenOS tools never run apt at the same time.
  * every package shows where it comes from: official Debian archive,
    a third-party repository, or a local file.
  * local .deb files open in the existing graphical installer
    (luxenos-deb-install: confirmation, checksum, polkit).

Command line: luxenos-software [--updates | --install PKG... | FILE.deb]

Growth path: the catalogue is package-based. Adding AppStream (DEP-11)
metadata later only changes load_catalogue(); the operation pipeline stays.
"""

import os
import sys
import threading

from . import config, pkg, ui

APP_ID = "org.luxenos.SoftwareCenter"


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    debs = [a for a in argv[1:] if a.endswith(".deb") or a.startswith("file://")]
    if debs:
        # Local packages: the dedicated .deb workflow (shows the package,
        # verifies the file, asks polkit). One at a time.
        os.execvp("luxenos-deb-install", ["luxenos-deb-install", debs[0]])

    Gtk = ui.init()
    from gi.repository import Gio, GLib, Pango

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Software")
            self.set_default_size(1000, 700)
            self.catalogue = []
            self.by_name = {}
            self.busy = False
            self.category = None
            self.current = None
            self.log_lines = []

            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(ui.transition_ms(150))
            self.switcher = Gtk.StackSwitcher(stack=self.stack)
            header.set_title_widget(self.switcher)
            self.refresh_btn = ui.icon_button("view-refresh-symbolic", "Refresh catalogue (download package lists)",
                                              lambda: self.run_helper(["update-lists"], "Refreshing catalogue"),
                                              css="flat")
            header.pack_start(self.refresh_btn)
            deb_btn = Gtk.Button(label="Install .deb file…")
            deb_btn.connect("clicked", lambda *_: self.pick_deb())
            header.pack_end(deb_btn)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "top"):
                getattr(self.banners, "set_margin_" + m)(10)
            outer.append(self.banners)
            outer.append(self.stack)
            outer.append(self.build_progress())
            self.set_child(outer)

            self.build_explore()
            self.build_installed()
            self.build_updates()
            self.build_details()
            self.connect("close-request", self.on_close)
            self.reload()

        # -- layout --
        def build_explore(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(12)
            self.search = Gtk.SearchEntry(placeholder_text="Search software by name or description")
            self.search.connect("search-changed", lambda *_: self.fill_explore())
            box.append(self.search)
            cats = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                               column_spacing=6, row_spacing=6, homogeneous=True)
            self.cat_buttons = {}
            for key, title, icon, _sections in (("", "All", "view-app-grid-symbolic", ()),) + pkg.CATEGORIES:
                b = Gtk.ToggleButton()
                hb = Gtk.Box(spacing=6)
                hb.append(Gtk.Image.new_from_icon_name(icon))
                hb.append(Gtk.Label(label=title))
                b.set_child(hb)
                if self.cat_buttons:
                    b.set_group(next(iter(self.cat_buttons.values())))
                b.connect("toggled", self.on_category, key or None)
                self.cat_buttons[key] = b
                cats.append(b)
            box.append(cats)
            opts = Gtk.Box(spacing=12)
            self.libs = Gtk.CheckButton(label="Show libraries and developer packages")
            self.libs.connect("toggled", lambda *_: self.fill_explore())
            opts.append(self.libs)
            self.explore_count = ui.label("", "lx-dim lx-small")
            self.explore_count.set_hexpand(True)
            self.explore_count.set_xalign(1)
            opts.append(self.explore_count)
            box.append(opts)
            self.explore_list = self.make_list()
            box.append(self.scrolled(self.explore_list))
            self.cat_buttons[""].set_active(True)
            self.stack.add_titled(box, "explore", "Explore")

        def build_installed(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(12)
            self.inst_search = Gtk.SearchEntry(placeholder_text="Filter installed software")
            self.inst_search.connect("search-changed", lambda *_: self.fill_installed())
            box.append(self.inst_search)
            self.inst_all = Gtk.CheckButton(label="Include automatically installed dependencies")
            self.inst_all.connect("toggled", lambda *_: self.fill_installed())
            box.append(self.inst_all)
            self.inst_list = self.make_list()
            box.append(self.scrolled(self.inst_list))
            self.stack.add_titled(box, "installed", "Installed")

        def build_updates(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(12)
            top = Gtk.Box(spacing=8)
            self.upd_label = ui.label("", "lx-heading")
            self.upd_label.set_hexpand(True)
            top.append(self.upd_label)
            self.upd_btn = Gtk.Button(label="Update all")
            self.upd_btn.add_css_class("suggested-action")
            self.upd_btn.connect("clicked", lambda *_: self.start_operation("upgrade", []))
            top.append(self.upd_btn)
            box.append(top)
            box.append(ui.label("Updates come from the repositories enabled on this system. A restore point of "
                                "the system configuration is created first when automatic backups are on "
                                "(Restore Center).", "lx-dim lx-small", wrap=True))
            self.upd_list = self.make_list()
            box.append(self.scrolled(self.upd_list))
            self.stack.add_titled(box, "updates", "Updates")

        def build_details(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(16)
            back = Gtk.Button(label="← Back")
            back.set_halign(Gtk.Align.START)
            back.connect("clicked", lambda *_: self.stack.set_visible_child_name(self.back_to))
            box.append(back)
            head = Gtk.Box(spacing=12)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            self.d_name = ui.label("", "lx-title", selectable=True)
            self.d_summary = ui.label("", "lx-dim", wrap=True)
            vb.append(self.d_name)
            vb.append(self.d_summary)
            head.append(vb)
            self.d_action = Gtk.Button()
            self.d_action.set_valign(Gtk.Align.CENTER)
            self.d_action.connect("clicked", lambda *_: self.details_action())
            head.append(self.d_action)
            self.d_remove = Gtk.Button(label="Remove")
            self.d_remove.add_css_class("destructive-action")
            self.d_remove.set_valign(Gtk.Align.CENTER)
            self.d_remove.connect("clicked", lambda *_: self.current and
                                  self.start_operation("remove", [self.current.name]))
            head.append(self.d_remove)
            box.append(head)
            self.d_badges = Gtk.Box(spacing=6)
            box.append(self.d_badges)
            self.d_origin = ui.label("", "lx-small", wrap=True)
            box.append(self.d_origin)
            self.d_facts = ui.label("", "lx-dim lx-small", wrap=True, selectable=True)
            box.append(self.d_facts)
            self.d_desc = ui.label("", None, wrap=True, selectable=True)
            self.d_desc.set_valign(Gtk.Align.START)
            box.append(self.scrolled(self.d_desc))
            self.stack.add_named(box, "details")
            self.back_to = "explore"

        def build_progress(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for m in ("start", "end", "bottom"):
                getattr(box, "set_margin_" + m)(10)
            self.progress = Gtk.ProgressBar(show_text=True)
            box.append(self.progress)
            self.prog_label = ui.label("", "lx-small", wrap=True)
            box.append(self.prog_label)
            self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            exp = Gtk.Expander(label="Details")
            sw = Gtk.ScrolledWindow(min_content_height=140)
            sw.set_child(self.log_view)
            exp.set_child(sw)
            box.append(exp)
            box.set_visible(False)
            self.progress_box = box
            return box

        def make_list(self):
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("boxed-list")
            lb.connect("row-activated", lambda _l, row: self.show_details(row.info))
            return lb

        def scrolled(self, child):
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(child)
            return sw

        def badge(self, text, css):
            b = ui.label(text, "lx-badge " + css)
            b.set_valign(Gtk.Align.CENTER)
            return b

        def row_for(self, info):
            row = Gtk.ListBoxRow()
            row.info = info
            hb = Gtk.Box(spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(hb, "set_margin_" + m)(6)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = ui.label(info.name, "lx-heading")
            vb.append(name)
            s = ui.label(info.summary, "lx-dim lx-small")
            s.set_ellipsize(Pango.EllipsizeMode.END)
            vb.append(s)
            hb.append(vb)
            text, css = pkg.BADGES[info.origin_kind]
            hb.append(self.badge(text, css))
            if info.upgradable:
                hb.append(self.badge("Update", "thirdparty"))
            elif info.installed:
                hb.append(self.badge("Installed", "installed"))
            row.set_child(hb)
            ui.accessible_label(row, "%s: %s. %s%s" % (info.name, info.summary, text,
                                                       ", installed" if info.installed else ""))
            return row

        def fill(self, listbox, items, empty):
            listbox.remove_all()
            if not items:
                row = Gtk.ListBoxRow(activatable=False)
                row.set_child(ui.label(empty, "lx-dim"))
                listbox.append(row)
                return
            for info in items:
                listbox.append(self.row_for(info))

        # -- data --
        def reload(self):
            self.set_banners()
            self.prog_status("Loading the software catalogue…", pulse=True)

            def work():
                try:
                    cat = pkg.load_catalogue()
                    err = None
                except Exception as e:  # python3-apt missing or a corrupt cache
                    cat, err = [], str(e)
                GLib.idle_add(self.loaded, cat, err)

            threading.Thread(target=work, daemon=True).start()

        def loaded(self, cat, err):
            self.catalogue = cat
            self.by_name = {p.name: p for p in cat}
            self.progress_box.set_visible(self.busy)
            if err:
                self.add_banner("error", "The software catalogue could not be read: %s" % err)
            self.fill_explore()
            self.fill_installed()
            self.fill_updates()
            pending = getattr(self.get_application(), "pending", None)
            if pending:
                self.get_application().pending = None
                pending(self)
            return False

        def set_banners(self):
            child = self.banners.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.banners.remove(child)
                child = nxt
            if config.is_live_session():
                self.add_banner("warning", "You are running the live system: software installed now is lost "
                                           "when the computer shuts down.")
            if not pkg.lists_present():
                self.add_banner("info", "The software catalogue has not been downloaded yet. Use Refresh "
                                        "(needs internet) to see available software.",
                                "Refresh", lambda: self.run_helper(["update-lists"], "Refreshing catalogue"))
            if pkg.dpkg_interrupted():
                self.add_banner("error", "A previous installation was interrupted, so other changes may fail.",
                                "Repair", lambda: self.run_helper(["repair"], "Repairing"))

        def add_banner(self, level, text, button=None, callback=None):
            hb = Gtk.Box(spacing=10)
            hb.add_css_class("lx-banner")
            hb.add_css_class(level)
            lbl = ui.label(text, None, wrap=True)
            lbl.set_hexpand(True)
            hb.append(lbl)
            if button:
                b = Gtk.Button(label=button)
                b.connect("clicked", lambda *_: callback())
                hb.append(b)
            self.banners.append(hb)

        def on_category(self, btn, key):
            if btn.get_active():
                self.category = key
                self.fill_explore()

        def fill_explore(self):
            q = self.search.get_text()
            if not q and self.category is None:
                items = [self.by_name[n] for n, _ in pkg.SUGGESTED if n in self.by_name]
                self.explore_count.set_text("Suggested apps — search or pick a category to see more")
                self.fill(self.explore_list, items, "No catalogue yet: use Refresh.")
                return
            items = pkg.search(self.catalogue, q, self.category, self.libs.get_active())
            self.explore_count.set_text("%d result%s%s" % (len(items), "" if len(items) == 1 else "s",
                                                            " (first 300)" if len(items) >= 300 else ""))
            self.fill(self.explore_list, items, "Nothing found.")

        def fill_installed(self):
            q = self.inst_search.get_text().lower()
            items = [p for p in self.catalogue if p.installed and (self.inst_all.get_active() or not p.auto)
                     and (not q or q in p.name or q in p.summary.lower())]
            self.fill(self.inst_list, items[:500], "Nothing installed matches.")

        def fill_updates(self):
            items = [p for p in self.catalogue if p.upgradable]
            n = len(items)
            self.upd_label.set_text("%d update%s available" % (n, "" if n == 1 else "s") if n else
                                    "Everything is up to date (as of the last catalogue refresh)")
            self.upd_btn.set_sensitive(n > 0 and not self.busy)
            self.fill(self.upd_list, items, "No updates.")
            self.stack.get_page(self.stack.get_child_by_name("updates")).set_title(
                "Updates (%d)" % n if n else "Updates")

        def show_details(self, info):
            self.current = info
            if self.stack.get_visible_child_name() != "details":
                self.back_to = self.stack.get_visible_child_name()
            self.d_name.set_text(info.name)
            self.d_summary.set_text(info.summary)
            child = self.d_badges.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.d_badges.remove(child)
                child = nxt
            text, css = pkg.BADGES[info.origin_kind]
            self.d_badges.append(self.badge(text, css))
            if info.installed:
                self.d_badges.append(self.badge("Installed", "installed"))
            self.d_origin.set_text(info.origin_text)
            facts = []
            if info.installed_version:
                facts.append("Installed version: %s" % info.installed_version)
            if info.candidate_version and info.candidate_version != info.installed_version:
                facts.append("Available version: %s" % info.candidate_version)
            facts.append("Section: %s" % (info.section or "unknown"))
            if info.size:
                facts.append("Installed size: %s" % ui.human_bytes(info.size))
            if info.homepage:
                facts.append("Homepage: %s" % info.homepage)
            self.d_facts.set_text("\n".join(facts))
            self.d_desc.set_text("Loading description…")
            threading.Thread(target=lambda: GLib.idle_add(self.d_desc.set_text, pkg.describe(info.name) or
                                                          info.summary), daemon=True).start()
            self.update_detail_buttons()
            self.stack.set_visible_child_name("details")

        def update_detail_buttons(self):
            info = self.current
            if info is None:
                return
            for css in ("suggested-action",):
                self.d_action.remove_css_class(css)
            if info.upgradable:
                self.d_action.set_label("Update")
                self.d_action.set_visible(True)
            elif info.installed:
                self.d_action.set_visible(False)
            else:
                self.d_action.set_label("Install")
                self.d_action.set_visible(info.candidate_version is not None)
            self.d_action.add_css_class("suggested-action")
            self.d_action.set_sensitive(not self.busy and info.origin_kind != "unverified")
            self.d_remove.set_visible(info.installed)
            self.d_remove.set_sensitive(not self.busy)

        def details_action(self):
            if self.current is not None:
                self.start_operation("install", [self.current.name])

        # -- operations --
        def start_operation(self, action, names):
            if self.busy:
                return
            self.prog_status("Checking what this will change…", pulse=True)

            def work():
                try:
                    sim = pkg.simulate(action, names)
                except (OSError, ValueError) as e:
                    sim = {"errors": [str(e)], "install": [], "upgrade": [], "remove": []}
                GLib.idle_add(self.confirm, action, names, sim)

            threading.Thread(target=work, daemon=True).start()

        def confirm(self, action, names, sim):
            self.progress_box.set_visible(False)
            if sim["errors"]:
                summary, _ = pkg.diagnose(sim["errors"])
                ui.alert(self, "This change is not possible right now", summary + "\n\n" + "\n".join(sim["errors"]))
                return False
            protected = [r for r in sim["remove"] if r.split(":")[0] in pkg.PROTECTED]
            if protected:
                ui.alert(self, "Refused: LuxenOS needs these packages",
                         "This would remove %s, which the desktop or system cannot run without." %
                         ", ".join(protected))
                return False
            lines = []
            extra_inst = [n for n in sim["install"] if n.split(":")[0] not in names]
            if action == "upgrade":
                lines.append("%d package%s will be updated." % (len(sim["upgrade"]), "" if len(sim["upgrade"]) == 1 else "s"))
            if extra_inst:
                lines.append("Also installs: " + ", ".join(extra_inst[:25]) + (" …" if len(extra_inst) > 25 else ""))
            if sim["remove"]:
                lines.append("REMOVES: " + ", ".join(sim["remove"][:25]) + (" …" if len(sim["remove"]) > 25 else ""))
            third = [n for n in names if n in self.by_name and self.by_name[n].origin_kind == "thirdparty"]
            if third:
                lines.append("\nNot from Debian: %s comes from a third-party repository. Only continue if you "
                             "trust that source." % ", ".join(third))
            lines.append("\nYou will be asked for an administrator password.")
            if action == "install":
                heading, button = "Install %s?" % ", ".join(names), "Install"
            elif action == "remove":
                heading, button = "Remove %s?" % ", ".join(names), "Remove"
                lines.insert(0, "Settings in your home folder are kept.")
            else:
                heading, button = "Install all updates?", "Update"
            if not sim["install"] and not sim["upgrade"] and not sim["remove"]:
                ui.alert(self, "Nothing to do", "apt reports no changes are needed.")
                self.reload()
                return False

            def chosen(idx):
                if idx == 1:
                    self.run_helper([action] + list(names), heading.rstrip("?"))

            ui.alert(self, heading, "\n".join(lines), buttons=("Cancel", button), default=1, cancel=0,
                     callback=chosen)
            return False

        def run_helper(self, args, title):
            if self.busy:
                return
            if not os.access(pkg.ROOT_HELPER, os.X_OK):
                ui.alert(self, "Software changes are unavailable", "%s is missing." % pkg.ROOT_HELPER)
                return
            self.set_busy(True)
            self.log_lines = []
            self.log_view.get_buffer().set_text("")
            self.prog_status(title + "…", pulse=True)
            try:
                proc = Gio.Subprocess.new(["pkexec", pkg.ROOT_HELPER] + list(args),
                                          Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            except GLib.Error as e:
                self.finish(title, False, "Could not start the helper: %s" % e.message)
                return
            stream = Gio.DataInputStream.new(proc.get_stdout_pipe())
            self.pulse_id = GLib.timeout_add(150, self.pulse)

            def read_line(s, res):
                try:
                    line, _len = s.read_line_finish_utf8(res)
                except GLib.Error:
                    line = None
                if line is None:
                    proc.wait_check_async(None, done)
                    return
                self.handle_line(line)
                s.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

            def done(p, res):
                try:
                    p.wait_check_finish(res)
                    ok = True
                except GLib.Error:
                    ok = False
                status = p.get_exit_status()
                if status in (126, 127) and not self.log_lines:
                    self.finish(title, False, "Authentication was cancelled or failed; nothing was changed.")
                else:
                    self.finish(title, ok, None)

            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, read_line)

        def pulse(self):
            if self.progress.get_fraction() == 0 or self.pulsing:
                self.progress.pulse()
            return self.busy

        def handle_line(self, line):
            kind, pct, text = pkg.parse_status_line(line)
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text + "\n")
            if kind == "status":
                self.prog_label.set_text(text)
            elif kind in ("install", "download") and pct is not None:
                self.pulsing = False
                self.progress.set_fraction(max(0.0, min(1.0, pct / 100.0)))
                self.progress.set_text(("Downloading" if kind == "download" else "Installing") + " %d%%" % pct)
                self.prog_label.set_text(text)
            elif kind == "log":
                self.log_lines.append(text)

        def finish(self, title, ok, message):
            self.set_busy(False)
            if ok:
                self.progress.set_fraction(1.0)
                self.progress.set_text("Done")
                self.prog_label.set_text("%s: finished." % title)
            else:
                summary, repair = pkg.diagnose(self.log_lines)
                msg = message or summary
                self.progress.set_text("Failed")
                self.prog_label.set_text(msg)
                body = msg
                if self.log_lines and not message:
                    body += "\n\n" + "\n".join(self.log_lines[-12:])
                body += "\n\nFull log: /var/log/luxenos/pkg-helper.log"
                if repair:
                    ui.alert(self, "%s failed" % title, body, buttons=("Close", "Repair"), default=1, cancel=0,
                             callback=lambda i: i == 1 and GLib.idle_add(
                                 lambda: self.run_helper(["repair"], "Repairing") or False))
                else:
                    ui.alert(self, "%s failed" % title, body)
            self.reload_after_operation()

        def reload_after_operation(self):
            name = self.current.name if self.current is not None else None

            def after(win):
                if name and name in win.by_name and win.stack.get_visible_child_name() == "details":
                    win.show_details(win.by_name[name])
                win.progress_box.set_visible(True)

            self.get_application().pending = after
            self.reload()

        def set_busy(self, busy):
            self.busy = busy
            self.pulsing = busy
            self.refresh_btn.set_sensitive(not busy)
            self.upd_btn.set_sensitive(not busy and any(p.upgradable for p in self.catalogue))
            self.update_detail_buttons()
            self.progress_box.set_visible(True)
            if not busy:
                self.progress.set_fraction(self.progress.get_fraction())

        def prog_status(self, text, pulse=False):
            self.progress_box.set_visible(True)
            self.prog_label.set_text(text)
            if pulse:
                self.progress.set_text("")
                self.progress.pulse()

        def pick_deb(self):
            dialog = Gtk.FileDialog(title="Choose a Debian package")
            f = Gtk.FileFilter()
            f.set_name("Debian packages (*.deb)")
            f.add_pattern("*.deb")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(f)
            dialog.set_filters(filters)

            def done(d, res):
                try:
                    gfile = d.open_finish(res)
                except GLib.Error:
                    return
                if gfile is not None and gfile.get_path():
                    if not ui.spawn(["luxenos-deb-install", gfile.get_path()]):
                        ui.alert(self, "The .deb installer is not available")

            dialog.open(self, None, done)

        def on_close(self, *_):
            if self.busy:
                ui.alert(self, "Please wait", "Software is being installed or removed. Closing the window now "
                                              "would not stop it, but you would not see the result. The window "
                                              "can be closed when it has finished.")
                return True
            return False

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.pending = None

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_command_line(self, cmdline):
            args = cmdline.get_arguments()[1:]
            win = self.get_active_window()
            created = win is None
            if created:
                win = Window(self)
            action = None
            if "--updates" in args:
                def action(w):
                    w.stack.set_visible_child_name("updates")
            elif "--install" in args:
                names = [a for a in args[args.index("--install") + 1:] if pkg.valid_name(a)]

                def action(w):
                    found = [n for n in names if n in w.by_name and not w.by_name[n].installed]
                    if found:
                        w.start_operation("install", found)
                    elif names:
                        ui.alert(w, "Nothing to install", "These apps are already installed or not in the "
                                                          "catalogue (refresh it first): %s" % ", ".join(names))
            if action is not None:
                if created or not win.catalogue:
                    self.pending = action
                else:
                    action(win)
            win.present()
            return 0

    return App().run(argv)
