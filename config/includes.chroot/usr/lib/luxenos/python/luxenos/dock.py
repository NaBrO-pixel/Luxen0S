"""LuxenOS dock: pinned and running applications on a glass panel.

* Running windows come from the Sway IPC tree and are kept current by IPC
  window/workspace/output events (no polling). Windows are grouped by app_id
  (Wayland) or WM class (Xwayland) and matched to desktop entries.
* Click: start the app; focus its window; click the focused window again to
  minimize it (Sway's scratchpad is the minimized state), and again to restore
  it. Apps with several windows cycle through them. Middle click opens a new
  window, right click (or the Menu key) lists every window.
* One dock per monitor, recreated when monitors come and go. A second
  `luxenos-dock` start is a no-op (GApplication single instance), so Sway
  reloads and session restarts never stack docks.
* Settings live in ~/.config/luxenos/dock.conf over /etc/luxenos/dock.conf
  (KEY=VALUE); `luxenos-dock config KEY VALUE` and the Settings app write
  them, the running dock picks changes up immediately.

Needs wlr-layer-shell (Sway/SwayFX); without it the dock reports that and exits.
"""

import os
import sys
import threading
import time

from . import apps as appsmod
from . import config, swayipc, ui

APP_ID = "org.luxenos.Dock"
SYS_CONF = os.environ.get("LUXENOS_DOCK_SYS_CONF", "/etc/luxenos/dock.conf")
SCRATCH = "__i3_scratch"

DEFAULTS = {
    "DOCK_ENABLE": True,
    "DOCK_POSITION": "bottom",
    "DOCK_ICON_SIZE": 44,
    "DOCK_SPACING": 6,
    "DOCK_AUTOHIDE": False,
    "DOCK_MONITORS": "all",
    "DOCK_PINNED": [],
}
KEYS = tuple(DEFAULTS)


def user_conf():
    return os.path.join(config.luxenos_config_dir(), "dock.conf")


def parse_settings(values):
    """Validated settings from raw KEY=VALUE pairs (invalid -> default)."""
    pinned = values.get("DOCK_PINNED", "")
    return {
        "DOCK_ENABLE": config.get_bool(values, "DOCK_ENABLE", True),
        "DOCK_POSITION": config.get_choice(values, "DOCK_POSITION", "bottom", ("bottom", "left", "right")),
        "DOCK_ICON_SIZE": config.get_int(values, "DOCK_ICON_SIZE", 44, 24, 96),
        "DOCK_SPACING": config.get_int(values, "DOCK_SPACING", 6, 0, 24),
        "DOCK_AUTOHIDE": config.get_bool(values, "DOCK_AUTOHIDE", False),
        "DOCK_MONITORS": config.get_choice(values, "DOCK_MONITORS", "all", ("all", "first")),
        "DOCK_PINNED": [p for p in pinned.split() if valid_app_id(p)],
    }


def valid_app_id(app_id):
    return bool(app_id) and app_id.endswith(".desktop") and len(app_id) < 256 and \
        all(c.isalnum() or c in "._-+" for c in app_id)


def load_settings():
    return parse_settings(config.read_layered(SYS_CONF, user_conf()))


def validate_setting(key, value):
    """Return the normalised value to store, or raise ValueError."""
    if key not in KEYS:
        raise ValueError("unknown setting %s (known: %s)" % (key, " ".join(KEYS)))
    probe = parse_settings({key: value})
    stored = probe[key]
    if key == "DOCK_PINNED":
        if any(not valid_app_id(p) for p in value.split()):
            raise ValueError("DOCK_PINNED takes desktop file ids such as firefox-esr.desktop")
        return " ".join(stored)
    if isinstance(stored, bool):
        if value.lower() not in ("yes", "no", "true", "false", "1", "0", "on", "off"):
            raise ValueError("%s takes yes or no" % key)
        return "yes" if stored else "no"
    if str(stored) != value.strip():
        raise ValueError("invalid value %r for %s" % (value, key))
    return str(stored)


def set_setting(key, value):
    stored = validate_setting(key, value)
    config.update_kv(user_conf(), {key: stored},
                     header="# LuxenOS dock settings for this user. See /etc/luxenos/dock.conf.")
    return stored


def set_pinned(app_id, pin):
    pinned = load_settings()["DOCK_PINNED"]
    if pin and app_id not in pinned:
        pinned.append(app_id)
    elif not pin and app_id in pinned:
        pinned.remove(app_id)
    set_setting("DOCK_PINNED", " ".join(pinned))


# --- grouping (pure, unit tested) --------------------------------------------

class DockItem:
    def __init__(self, key, entry, pinned):
        self.key = key            # desktop id or bare app_id for unknown apps
        self.entry = entry        # AppEntry or None
        self.pinned = pinned
        self.windows = []         # [(node, workspace)]

    @property
    def name(self):
        if self.entry is not None:
            return self.entry.name
        return self.key

    @property
    def focused(self):
        return any(w.get("focused") for w, _ in self.windows)

    def visible_windows(self):
        return [(w, ws) for w, ws in self.windows if ws != SCRATCH]

    def minimized_windows(self):
        return [(w, ws) for w, ws in self.windows if ws == SCRATCH]


def build_items(tree, pinned_ids, index, entries_by_id):
    items = []
    by_key = {}
    for app_id in pinned_ids:
        entry = entries_by_id.get(app_id)
        if entry is None:
            continue  # pinned app was uninstalled
        item = DockItem(app_id, entry, True)
        items.append(item)
        by_key[app_id] = item
    if tree is not None:
        for node, ws, _out in swayipc.iter_windows(tree):
            wkey = swayipc.window_app_key(node)
            entry = index.lookup(wkey)
            key = entry.app_id if entry is not None else (wkey or "unknown")
            item = by_key.get(key)
            if item is None:
                item = DockItem(key, entry, False)
                items.append(item)
                by_key[key] = item
            item.windows.append((node, ws))
    for item in items:
        item.windows.sort(key=lambda t: t[0].get("id", 0))
    return items


def next_window(item):
    """Window to focus on click: the one after the focused one, cycling."""
    visible = item.visible_windows()
    if not visible:
        return None
    ids = [w.get("id") for w, _ in visible]
    for i, (w, _) in enumerate(visible):
        if w.get("focused"):
            return visible[(i + 1) % len(visible)][0]
    return visible[0][0]


# --- command line -------------------------------------------------------------

USAGE = """usage: luxenos-dock                    start the dock (no-op if running)
       luxenos-dock --focus            move keyboard focus to the dock
       luxenos-dock config             show settings
       luxenos-dock config KEY VALUE   change a setting (DOCK_POSITION bottom|left|right,
                                       DOCK_ICON_SIZE 24-96, DOCK_SPACING 0-24,
                                       DOCK_AUTOHIDE yes|no, DOCK_MONITORS all|first,
                                       DOCK_ENABLE yes|no)
       luxenos-dock pin|unpin APP.desktop"""


def cli(argv):
    if len(argv) >= 2 and argv[1] == "config":
        if len(argv) == 2:
            s = load_settings()
            for k in KEYS:
                v = s[k]
                print("%s=%s" % (k, " ".join(v) if isinstance(v, list) else
                                 ("yes" if v is True else "no" if v is False else v)))
            return 0
        if len(argv) == 4:
            try:
                print("%s=%s" % (argv[2], set_setting(argv[2], argv[3])))
                return 0
            except ValueError as e:
                print("luxenos-dock: %s" % e, file=sys.stderr)
                return 2
    if len(argv) == 3 and argv[1] in ("pin", "unpin"):
        if not valid_app_id(argv[2]):
            print("luxenos-dock: expected a desktop file id like firefox-esr.desktop", file=sys.stderr)
            return 2
        set_pinned(argv[2], argv[1] == "pin")
        return 0
    if len(argv) >= 2 and argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    rc = cli(argv)
    if rc is not None:
        return rc

    Gtk = ui.init(layer_shell=True)
    from gi.repository import Gdk, Gio, GLib

    class DockWindow(Gtk.ApplicationWindow):
        def __init__(self, app, monitor):
            super().__init__(application=app, title="Dock")
            self.app = app
            self.monitor = monitor
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            self.hide_source = None
            self.revealer = Gtk.Revealer()
            self.panel = Gtk.Box()
            self.panel.add_css_class("lx-glass")
            self.panel.add_css_class("lx-dock")
            self.revealer.set_child(self.panel)
            self.handle = Gtk.Box()
            self.handle.add_css_class("lx-dock-handle")
            self.outer = Gtk.Box()
            self.set_child(self.outer)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self.reveal(True))
            motion.connect("leave", lambda *_: self.schedule_hide())
            self.add_controller(motion)
            ui.add_escape_handler(self, self.release_focus)
            self.configure()

        def configure(self):
            s = self.app.settings
            pos = s["DOCK_POSITION"]
            horizontal = pos == "bottom"
            margin = 6
            anchors = (pos,)
            ui.make_layer(self, "luxenos-dock", layer="top", anchors=anchors,
                          margins={pos: margin}, keyboard="on_demand",
                          exclusive_zone=0 if s["DOCK_AUTOHIDE"] else "auto", monitor=self.monitor)
            orient = Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL
            self.panel.set_orientation(orient)
            self.panel.set_spacing(s["DOCK_SPACING"])
            self.outer.set_orientation(Gtk.Orientation.VERTICAL if horizontal else Gtk.Orientation.HORIZONTAL)
            child = self.outer.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.outer.remove(child)
                child = nxt
            self.handle.set_size_request(-1 if horizontal else 6, 6 if horizontal else -1)
            transition = {
                "bottom": Gtk.RevealerTransitionType.SLIDE_UP,
                "left": Gtk.RevealerTransitionType.SLIDE_RIGHT,
                "right": Gtk.RevealerTransitionType.SLIDE_LEFT,
            }[pos]
            if ui.reduce_motion():
                transition = Gtk.RevealerTransitionType.NONE
            self.revealer.set_transition_type(transition)
            self.revealer.set_transition_duration(ui.transition_ms(180))
            if pos == "right":
                self.outer.append(self.handle)
                self.outer.append(self.revealer)
            else:
                self.outer.append(self.revealer)
                self.outer.append(self.handle)
            self.handle.set_visible(s["DOCK_AUTOHIDE"])
            self.revealer.set_reveal_child(not s["DOCK_AUTOHIDE"])

        def reveal(self, show):
            if self.hide_source:
                GLib.source_remove(self.hide_source)
                self.hide_source = None
            if show or not self.app.settings["DOCK_AUTOHIDE"]:
                self.revealer.set_reveal_child(True)

        def schedule_hide(self):
            if not self.app.settings["DOCK_AUTOHIDE"] or self.hide_source:
                return
            self.hide_source = GLib.timeout_add(700, self.do_hide)

        def do_hide(self):
            self.hide_source = None
            if self.app.menu_open or self.has_focus_within():
                return False
            self.revealer.set_reveal_child(False)
            return False

        def has_focus_within(self):
            return self.is_active() and ui.LAYER_SHELL and \
                ui.LayerShell.get_keyboard_mode(self) == ui.LayerShell.KeyboardMode.EXCLUSIVE

        def take_focus(self):
            ui.set_keyboard_mode(self, "exclusive")
            self.reveal(True)
            self.present()
            first = self.panel.get_first_child()
            if first is not None:
                first.grab_focus()

        def release_focus(self):
            ui.set_keyboard_mode(self, "on_demand")
            self.schedule_hide()

        def populate(self, items):
            s = self.app.settings
            child = self.panel.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.panel.remove(child)
                child = nxt
            seen_unpinned = False
            for item in items:
                if not item.pinned and not seen_unpinned and any(i.pinned for i in items):
                    sep = Gtk.Box()
                    sep.add_css_class("lx-dock-separator")
                    self.panel.append(sep)
                    seen_unpinned = True
                self.panel.append(self.make_button(item, s))
            if not items:
                self.panel.append(ui.label("No pinned apps", "lx-dim"))

        def make_button(self, item, s):
            size = s["DOCK_ICON_SIZE"]
            btn = Gtk.Button()
            btn.add_css_class("lx-dock-item")
            if item.focused:
                btn.add_css_class("focused")
            horizontal = s["DOCK_POSITION"] == "bottom"
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if horizontal else Gtk.Orientation.HORIZONTAL,
                          spacing=3)
            gicon = item.entry.info.get_icon() if item.entry is not None else None
            img = ui.gicon_image(gicon, size, "application-x-executable")
            dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL,
                           spacing=3, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            dots.set_size_request(5, 5)
            for i in range(min(len(item.windows), 3)):
                d = Gtk.Box()
                d.add_css_class("lx-dot")
                if item.focused and i == 0:
                    d.add_css_class("focused")
                dots.append(d)
            if s["DOCK_POSITION"] == "right":
                box.append(dots)
                box.append(img)
            else:
                box.append(img)
                box.append(dots)
            btn.set_child(box)
            n = len(item.windows)
            mini = len(item.minimized_windows())
            state = "not running" if n == 0 else ("%d window%s" % (n, "" if n == 1 else "s")) + \
                (", %d minimized" % mini if mini else "")
            ui.accessible_label(btn, "%s (%s)" % (item.name, state))
            btn.connect("clicked", lambda *_: self.app.primary_action(item))
            click = Gtk.GestureClick(button=0)
            click.connect("pressed", lambda g, _n, _x, _y: self.on_press(g, btn, item))
            btn.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _c, kv, _k, st: self.on_key(kv, st, btn, item))
            btn.add_controller(keys)
            return btn

        def on_press(self, gesture, btn, item):
            b = gesture.get_current_button()
            if b == 2:
                self.app.launch(item)
            elif b == 3:
                self.show_menu(btn, item)

        def on_key(self, keyval, state, btn, item):
            if keyval == Gdk.KEY_Menu or (keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK):
                self.show_menu(btn, item)
                return True
            return False

        def show_menu(self, btn, item):
            pop = Gtk.Popover()
            pop.set_parent(btn)
            pop.set_position({"bottom": Gtk.PositionType.TOP, "left": Gtk.PositionType.RIGHT,
                              "right": Gtk.PositionType.LEFT}[self.app.settings["DOCK_POSITION"]])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(ui.label(item.name, "lx-heading"))

            def add(text, cb, css=None):
                b = Gtk.Button(label=text)
                b.add_css_class("flat")
                if css:
                    b.add_css_class(css)
                b.get_child().set_xalign(0)

                def clicked(*_):
                    pop.popdown()
                    cb()

                b.connect("clicked", clicked)
                box.append(b)
                return b

            for node, ws in item.windows:
                title = node.get("name") or item.name
                where = "minimized" if ws == SCRATCH else "workspace %s" % ws
                add("%s  —  %s" % (title[:60], where), lambda n=node: self.app.focus_window(n))
            if item.windows:
                box.append(Gtk.Separator())
            if item.entry is not None:
                add("New window", lambda: self.app.launch(item))
            if item.visible_windows():
                add("Minimize", lambda: [self.app.minimize(w) for w, _ in item.visible_windows()])
            if item.minimized_windows():
                add("Restore", lambda: [self.app.restore(w) for w, _ in item.minimized_windows()])
            if item.windows:
                add("Close %s" % ("window" if len(item.windows) == 1 else "all windows"),
                    lambda: [self.app.close_window(w) for w, _ in item.windows])
            if item.entry is not None:
                if item.pinned:
                    add("Unpin from dock", lambda: self.app.pin(item, False))
                else:
                    add("Pin to dock", lambda: self.app.pin(item, True))
            pop.set_child(box)
            self.app.menu_open = True

            def closed(*_):
                self.app.menu_open = False
                GLib.idle_add(pop.unparent)
                self.schedule_hide()

            pop.connect("closed", closed)
            pop.popup()

    class DockApp(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.add_main_option("focus", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                                 "Move keyboard focus to the dock", None)
            self.windows = []
            self.settings = load_settings()
            self.entries = []
            self.entries_by_id = {}
            self.index = None
            self.tree = None
            self.refresh_source = None
            self.menu_open = False
            self.minimized_was_floating = {}
            self.started = False

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()
            self.theme_monitor = ui.watch_theme()

        def do_command_line(self, cmdline):
            opts = cmdline.get_options_dict().end().unpack()
            if not self.started:
                if not ui.LAYER_SHELL:
                    print("luxenos-dock: the compositor does not offer wlr-layer-shell "
                          "(or gtk4-layer-shell is missing); the dock needs it.", file=sys.stderr)
                    return 1
                self.started = True
                self.hold()
                self.load_entries()
                Gio.AppInfoMonitor.get().connect("changed", lambda *_: (self.load_entries(), self.schedule_refresh()))
                self.conf_monitors = []
                for path in (SYS_CONF, user_conf()):
                    d = Gio.File.new_for_path(os.path.dirname(path))
                    try:
                        m = d.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                    except GLib.Error:
                        continue
                    m.connect("changed", self.on_conf_changed)
                    self.conf_monitors.append(m)
                monitors = Gdk.Display.get_default().get_monitors()
                monitors.connect("items-changed", lambda *_: GLib.idle_add(self.rebuild_windows))
                self.rebuild_windows()
                threading.Thread(target=self.event_loop, daemon=True).start()
            if opts.get("focus") and self.windows:
                self.focused_dock().take_focus()
            return 0

        # -- data --
        def load_entries(self):
            self.entries = appsmod.load_apps(include_hidden=True)
            self.entries_by_id = {e.app_id: e for e in self.entries}
            self.index = appsmod.AppIndex(self.entries)

        def on_conf_changed(self, _m, f, _o, event):
            if f.get_basename() != "dock.conf":
                return
            if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                             Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.RENAMED,
                             Gio.FileMonitorEvent.DELETED):
                return
            new = load_settings()
            if new == self.settings:
                return
            layout_changed = any(new[k] != self.settings[k] for k in
                                 ("DOCK_POSITION", "DOCK_AUTOHIDE", "DOCK_MONITORS", "DOCK_ENABLE"))
            self.settings = new
            if layout_changed:
                self.rebuild_windows()
            else:
                for w in self.windows:
                    w.configure()
                self.schedule_refresh()

        def rebuild_windows(self):
            for w in self.windows:
                w.destroy()
            self.windows = []
            if not self.settings["DOCK_ENABLE"]:
                return False
            monitors = Gdk.Display.get_default().get_monitors()
            mons = [monitors.get_item(i) for i in range(monitors.get_n_items())]
            if self.settings["DOCK_MONITORS"] == "first":
                mons = mons[:1]
            for mon in mons:
                w = DockWindow(self, mon)
                self.windows.append(w)
                w.present()
            self.refresh()
            return False

        def focused_dock(self):
            focused_output = None
            try:
                for o in swayipc.Connection().get_outputs():
                    if o.get("focused"):
                        focused_output = o.get("name")
            except swayipc.IPCError:
                pass
            for w in self.windows:
                if w.monitor.get_connector() == focused_output:
                    return w
            return self.windows[0]

        def event_loop(self):
            while True:
                try:
                    conn = swayipc.Connection()
                    conn.subscribe(["window", "workspace", "output"])
                    GLib.idle_add(self.schedule_refresh)
                    while True:
                        swayipc.read_message(conn.sock)
                        GLib.idle_add(self.schedule_refresh)
                except (swayipc.IPCError, OSError, ValueError):
                    time.sleep(2)

        def schedule_refresh(self):
            if self.refresh_source is None:
                self.refresh_source = GLib.timeout_add(80, self.refresh)
            return False

        def refresh(self):
            self.refresh_source = None
            try:
                self.tree = swayipc.Connection().get_tree()
            except swayipc.IPCError:
                self.tree = None
            items = build_items(self.tree, self.settings["DOCK_PINNED"], self.index, self.entries_by_id)
            alive = {w.get("id") for i in items for w, _ in i.windows}
            for cid in list(self.minimized_was_floating):
                if cid not in alive:
                    del self.minimized_was_floating[cid]
            for w in self.windows:
                w.populate(items)
            return False

        # -- actions --
        def sway(self, cmd):
            try:
                swayipc.command(cmd)
                return True
            except swayipc.IPCError as e:
                print("luxenos-dock: sway refused %r: %s" % (cmd, e), file=sys.stderr)
                return False

        def launch(self, item):
            if item.entry is None:
                return
            ctx = Gdk.Display.get_default().get_app_launch_context()
            try:
                item.entry.info.launch([], ctx)
                appsmod.record_launch(item.entry.app_id)
            except GLib.Error as e:
                print("luxenos-dock: cannot start %s: %s" % (item.name, e.message), file=sys.stderr)

        def focus_window(self, node):
            if self.is_minimized(node):
                self.restore(node)
            else:
                self.sway("%s focus" % swayipc.con_criteria(node["id"]))

        def is_minimized(self, node):
            if self.tree is None:
                return False
            for w, ws, _ in swayipc.iter_windows(self.tree):
                if w.get("id") == node.get("id"):
                    return ws == SCRATCH
            return False

        def minimize(self, node):
            self.minimized_was_floating[node["id"]] = node.get("type") == "floating_con"
            self.sway("%s move scratchpad" % swayipc.con_criteria(node["id"]))

        def restore(self, node):
            crit = swayipc.con_criteria(node["id"])
            if self.sway("%s scratchpad show" % crit):
                if not self.minimized_was_floating.pop(node["id"], False):
                    self.sway("%s floating disable" % crit)
                self.sway("%s focus" % crit)

        def close_window(self, node):
            self.sway("%s kill" % swayipc.con_criteria(node["id"]))

        def primary_action(self, item):
            if not item.windows:
                self.launch(item)
                return
            visible = item.visible_windows()
            if item.focused and len(visible) == 1:
                self.minimize(visible[0][0])
                return
            if not visible:
                for node, _ in item.minimized_windows():
                    self.restore(node)
                return
            target = next_window(item)
            if target is not None:
                self.sway("%s focus" % swayipc.con_criteria(target["id"]))

        def pin(self, item, pin):
            try:
                set_pinned(item.key, pin)
            except (OSError, ValueError) as e:
                print("luxenos-dock: %s" % e, file=sys.stderr)
            self.settings = load_settings()
            self.schedule_refresh()

    return DockApp().run(argv)
