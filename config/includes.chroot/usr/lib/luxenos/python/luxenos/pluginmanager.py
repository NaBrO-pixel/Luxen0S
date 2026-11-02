"""Plugins: see, enable and disable LuxenOS plugins (luxenos.plugins).

Each plugin shows its version, author, what it may access (its declared
permissions) and, if it cannot be loaded, why (wrong plugin API version,
missing executable, unsafe file ownership). Enabling a plugin that asks for
network or file access asks for confirmation first. Plugins never run as
root and always run sandboxed (bubblewrap).
"""

import os
import shutil
import sys

from . import plugins, ui

APP_ID = "org.luxenos.Plugins"


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gio

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Plugins")
            self.set_default_size(720, 560)
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            folder = Gtk.Button(label="Open my plugin folder")
            folder.connect("clicked", lambda *_: self.open_folder())
            header.pack_end(folder)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(14)
            self.set_child(vb)
            vb.append(ui.label("Plugins add results to the launcher (Super+Space). They run as you, inside a "
                               "sandbox, with only the access listed under each one. Plugins you install yourself "
                               "(%s) start disabled." % plugins.user_dir(), "lx-dim lx-small", wrap=True))
            if not shutil.which("bwrap"):
                vb.append(ui.label("bubblewrap is not installed, so no plugin can run.", "lx-banner warning",
                                   wrap=True))
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class("boxed-list")
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(self.list)
            vb.append(sw)
            self.reload()

        def reload(self):
            self.list.remove_all()
            found = plugins.discover()
            if not found:
                self.list.append(ui.label("No plugins installed.", "lx-dim"))
            for p in sorted(found.values(), key=lambda x: x.name.lower()):
                self.list.append(self.row(p))

        def row(self, p):
            box = Gtk.Box(spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(8)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)
            vb.append(ui.label("%s %s" % (p.name, p.version), "lx-heading"))
            if p.description:
                vb.append(ui.label(p.description, "lx-small", wrap=True))
            perms = ", ".join(plugins.PERMISSIONS[x] for x in p.permissions if x in plugins.PERMISSIONS)
            vb.append(ui.label("%s · %s · access: %s" % (p.author or "unknown author",
                                                         "shipped with LuxenOS" if p.system else "installed by you",
                                                         perms or "none (no network, no files)"), "lx-dim lx-small",
                               wrap=True))
            for problem in p.problems:
                vb.append(ui.label("Cannot be loaded: " + problem, "lx-crit lx-small", wrap=True))
            box.append(vb)
            sw = Gtk.Switch(active=plugins.enabled(p) and p.usable, valign=Gtk.Align.CENTER)
            sw.set_sensitive(p.usable)
            ui.accessible_label(sw, "Enable %s" % p.name)
            sw.connect("state-set", lambda s, state: self.toggle(p, s, state))
            box.append(sw)
            return box

        def toggle(self, p, switch, state):
            if state and p.permissions and not plugins.enabled(p):
                perms = "\n".join("• " + plugins.PERMISSIONS[x] for x in p.permissions)
                ui.alert(self, "Allow %s?" % p.name, "This plugin asks to:\n%s" % perms, buttons=("Cancel", "Allow"),
                         default=0, cancel=0, callback=lambda i: self.commit(p, switch, i == 1))
                return True  # keep the switch where it is until answered
            self.commit(p, switch, state)
            return True

        def commit(self, p, switch, state):
            try:
                plugins.set_enabled(p.id, state)
            except (ValueError, OSError) as e:
                ui.alert(self, "Not changed", str(e))
                state = not state
            switch.set_state(state)
            switch.set_active(state)

        def open_folder(self):
            os.makedirs(plugins.user_dir(), exist_ok=True)
            ui.spawn(["xdg-open", plugins.user_dir()])

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv if argv is not None else sys.argv[:1])
