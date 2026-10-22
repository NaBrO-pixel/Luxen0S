"""LuxenOS System Restore Center.

Lists restore points (configuration backups made before updates or by
hand), explains exactly what a restore covers and what it does not, checks
a restore point, previews the changes it would make, and restores it after
an explicit confirmation. All privileged work is done by
/usr/libexec/luxenos/luxenos-restore-root through pkexec (polkit action
org.luxenos.restore, administrator password each time); the same tool works
without the desktop: `sudo luxenos-restore` on a text console.
"""

import os
import sys

from . import config, restore, ui

APP_ID = "org.luxenos.RestoreCenter"


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gio, Pango

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Restore Center")
            self.set_default_size(980, 640)
            self.busy = False
            self.selected = None
            self.items = []

            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.create_btn = Gtk.Button(label="Create restore point…")
            self.create_btn.add_css_class("suggested-action")
            self.create_btn.connect("clicked", lambda *_: self.ask_create())
            header.pack_start(self.create_btn)
            header.pack_end(ui.icon_button("view-refresh-symbolic", "Reload list", self.reload, css="flat"))

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(root, "set_margin_" + m)(12)
            self.set_child(root)
            note = ui.label(restore.snapshot_note(restore.root_fs()), "lx-banner info", wrap=True)
            root.append(note)
            if config.is_live_session():
                root.append(ui.label("This is the live system: restore points only exist on an installed "
                                     "LuxenOS.", "lx-banner warning", wrap=True))
                self.create_btn.set_sensitive(False)

            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
            paned.set_position(380)
            root.append(paned)

            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            self.list.add_css_class("boxed-list")
            self.list.connect("row-selected", self.on_select)
            sw = Gtk.ScrolledWindow()
            sw.set_child(self.list)
            sw.set_size_request(240, -1)
            paned.set_start_child(sw)
            paned.set_shrink_start_child(False)

            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            right.set_margin_start(12)
            self.d_title = ui.label("Select a restore point", "lx-title", wrap=True)
            right.append(self.d_title)
            self.d_meta = ui.label("", "lx-dim", wrap=True, selectable=True)
            right.append(self.d_meta)
            cover = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            cover.add_css_class("lx-card")
            cover.append(ui.label("A restore brings back", "lx-heading"))
            for c in restore.COVERS:
                cover.append(ui.label("• " + c, None, wrap=True))
            cover.append(ui.label("It does not change", "lx-heading"))
            for c in restore.NOT_COVERED:
                cover.append(ui.label("• " + c, None, wrap=True))
            right.append(cover)

            actions = Gtk.Box(spacing=6)
            self.verify_btn = Gtk.Button(label="Check")
            self.verify_btn.connect("clicked", lambda *_: self.run(["verify", self.selected.id], "Checking"))
            self.preview_btn = Gtk.Button(label="Preview changes")
            self.preview_btn.connect("clicked", lambda *_: self.run(["preview", self.selected.id], "Preview"))
            self.scope = Gtk.DropDown.new_from_strings(["Settings and packages", "Settings only", "Packages only"])
            ui.accessible_label(self.scope, "What to restore")
            self.restore_btn = Gtk.Button(label="Restore…")
            self.restore_btn.add_css_class("destructive-action")
            self.restore_btn.connect("clicked", lambda *_: self.confirm_restore())
            self.delete_btn = Gtk.Button(label="Delete")
            self.delete_btn.connect("clicked", lambda *_: self.confirm_delete())
            for w in (self.verify_btn, self.preview_btn, self.delete_btn):
                actions.append(w)
            right.append(actions)
            actions2 = Gtk.Box(spacing=6)
            actions2.append(self.scope)
            actions2.append(self.restore_btn)
            right.append(actions2)

            self.status = ui.label("", "lx-small", wrap=True)
            right.append(self.status)
            self.output = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            self.output.set_monospace(True)
            out_sw = Gtk.ScrolledWindow(vexpand=True, min_content_height=160)
            out_sw.set_child(self.output)
            right.append(out_sw)
            right.append(ui.label("If the desktop ever fails to start: press Ctrl+Alt+F2, log in, and run "
                                  "“sudo luxenos-restore”.", "lx-dim lx-small", wrap=True))
            # Vertical scrolling only: the pane's width is fixed, so labels wrap.
            right_sw = Gtk.ScrolledWindow()
            right_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            right_sw.set_child(right)
            paned.set_end_child(right_sw)
            paned.set_shrink_end_child(False)

            self.connect("close-request", self.on_close)
            self.reload()

        def reload(self):
            self.list.remove_all()
            self.points = restore.list_points()
            if not self.points:
                row = Gtk.ListBoxRow(activatable=False, selectable=False)
                row.set_child(ui.label("No restore points yet. One is created automatically before each "
                                       "update, or create one now.", "lx-dim", wrap=True))
                self.list.append(row)
            for p in self.points:
                row = Gtk.ListBoxRow()
                row.point = p
                vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                for m in ("start", "end", "top", "bottom"):
                    getattr(vb, "set_margin_" + m)(6)
                t = ui.label(p.description, "lx-heading")
                t.set_ellipsize(Pango.EllipsizeMode.END)
                vb.append(t)
                kind = {"update": "before an update", "restore": "before a restore",
                        "manual": "created by hand"}[p.trigger]
                vb.append(ui.label("%s · %s%s" % (p.created_text, kind, " · older format" if p.legacy else ""),
                                   "lx-dim lx-small"))
                row.set_child(vb)
                ui.accessible_label(row, "%s, %s, %s" % (p.description, p.created_text, kind))
                self.list.append(row)
            self.on_select(self.list, None)

        def on_select(self, _l, row):
            self.selected = getattr(row, "point", None) if row is not None else None
            p = self.selected
            for w in (self.verify_btn, self.preview_btn, self.restore_btn, self.delete_btn, self.scope):
                w.set_sensitive(p is not None and not self.busy and not config.is_live_session())
            if p is None:
                self.d_title.set_text("Select a restore point")
                self.d_meta.set_text("")
                return
            self.d_title.set_text(p.description)
            meta = ["Created: %s" % p.created_text, "Identifier: %s" % p.id]
            if p.info.get("LUXENOS_VERSION"):
                meta.append("LuxenOS: %s, kernel %s" % (p.info.get("LUXENOS_VERSION"), p.info.get("KERNEL", "?")))
            if p.info.get("PACKAGES"):
                meta.append("Packages recorded: %s" % p.info["PACKAGES"])
            meta.append("Integrity: " + ("no checksums (older format), checked by reading the archive" if p.legacy
                                         else "SHA-256 checksums, verified before every restore"))
            self.d_meta.set_text("\n".join(meta))

        # -- actions --
        def ask_create(self):
            dialog = Gtk.Window(title="Create restore point", modal=True, transient_for=self)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(16)
            box.append(ui.label("Saves the current system settings (/etc) and the list of installed packages.",
                                None, wrap=True))
            entry = Gtk.Entry(placeholder_text="Description, e.g. Before trying a new driver")
            entry.set_max_length(120)
            ui.accessible_label(entry, "Description")
            box.append(entry)
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda *_: dialog.close())
            ok = Gtk.Button(label="Create")
            ok.add_css_class("suggested-action")

            def go(*_):
                text = entry.get_text().strip() or "Restore point"
                dialog.close()
                self.run(["create", text], "Creating restore point")

            ok.connect("clicked", go)
            entry.connect("activate", go)
            row.append(cancel)
            row.append(ok)
            box.append(row)
            dialog.set_child(box)
            dialog.present()

        def confirm_restore(self):
            p = self.selected
            scope = ("all", "config", "packages")[self.scope.get_selected()]
            text = {"all": "system settings and installed packages", "config": "system settings",
                    "packages": "installed packages"}[scope]
            body = ("This puts %s back to how they were on %s.\n\n"
                    "The current state is saved as a new restore point first, so this can be undone.\n"
                    "Your files in /home are not touched. Accounts and passwords are kept.\n"
                    "Use “Preview changes” first to see exactly what changes.\n\n"
                    "Restart the computer afterwards." % (text, p.created_text))
            ui.alert(self, "Restore “%s”?" % p.description, body, buttons=("Cancel", "Restore"),
                     default=0, cancel=0,
                     callback=lambda i: i == 1 and self.run(["restore", p.id, scope], "Restoring"))

        def confirm_delete(self):
            p = self.selected
            ui.alert(self, "Delete “%s”?" % p.description, "It cannot be recovered afterwards.",
                     buttons=("Cancel", "Delete"), default=0, cancel=0,
                     callback=lambda i: i == 1 and self.run(["delete", p.id], "Deleting"))

        def run(self, args, title):
            if self.busy:
                return
            if not os.access(restore.ROOT_HELPER, os.X_OK):
                ui.alert(self, "Restore points are unavailable", "%s is missing." % restore.ROOT_HELPER)
                return
            self.busy = True
            self.create_btn.set_sensitive(False)
            self.on_select(self.list, self.list.get_selected_row())
            buf = self.output.get_buffer()
            buf.set_text("")
            self.status.set_text(title + "… (you will be asked for an administrator password)")
            self.errors = []

            def on_line(line):
                item = restore.parse_item(line)
                if item is not None:
                    section, text = item
                    prefix = {"summary": "\n", "config": "  settings: ", "packages": "  packages: ",
                              "verify": "  "}.get(section, "  ")
                    buf.insert(buf.get_end_iter(), prefix + text + "\n")
                elif line.startswith("@@STATUS "):
                    self.status.set_text(line[9:])
                elif line.startswith("@@LOG "):
                    self.errors.append(line[6:])
                    buf.insert(buf.get_end_iter(), line[6:] + "\n")
                elif not line.startswith("@@"):
                    buf.insert(buf.get_end_iter(), line + "\n")

            def on_done(rc):
                self.busy = False
                self.create_btn.set_sensitive(not config.is_live_session())
                if rc in (126, 127) and not self.errors:
                    self.status.set_text("Authentication was cancelled; nothing was changed.")
                elif rc != 0:
                    msg = self.errors[-1] if self.errors else "failed (exit status %d)" % rc
                    self.status.set_text("%s failed: %s" % (title, msg))
                    self.status.add_css_class("lx-crit")
                    if args[0] == "restore":
                        ui.alert(self, "The restore did not complete", msg + "\n\nThe state before the restore "
                                 "was saved as a restore point. From a text console, `sudo luxenos-restore` "
                                 "can apply it. Details: /var/log/luxenos/restore.log")
                    self.reload()
                    return
                self.status.remove_css_class("lx-crit")
                if args[0] in ("create", "delete", "restore"):
                    self.reload()
                if args[0] == "restore":
                    ui.alert(self, "Restore finished", "Restart the computer so all services use the restored "
                                                       "settings.")

            ui.run_streaming(["pkexec", restore.ROOT_HELPER] + list(args), on_line, on_done)

        def on_close(self, *_):
            if self.busy:
                ui.alert(self, "Please wait", "A restore-point operation is still running.")
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

    return App().run(argv if argv is not None else sys.argv)
