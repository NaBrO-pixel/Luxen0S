"""Workspace overview (Super+Tab).

A full-screen glass overlay on the focused monitor showing every workspace
on every output with its windows (title, application name and icon), read
from the Sway IPC tree. Nothing is captured from the screen, so it does not
depend on X11 or screencopy.

  * click / Enter on a window: focus it (Sway switches workspace as needed)
  * click a workspace title, or press 1-9: switch to that workspace
  * drag a window onto another workspace card, or use its menu (right click,
    Menu key or Shift+F10): move it there; "New workspace" creates the next
    free numbered one
  * type to filter windows by title or app; Escape closes
  * arrow keys move between windows, Enter focuses
  * each workspace card has a menu: rename (the number is kept, "2: Mail"),
    set or remove a wallpaper for that workspace (luxenos-wallpaper), and
    close the workspace (its windows move to another one; Sway removes empty
    workspaces by itself)
  * window previews: windows that are on screen when the overview opens are
    captured with grim (scaled down, never saved); other windows show their
    icon. Off with OVERVIEW_PREVIEWS=no in ~/.config/luxenos/overview.conf.

Moves use `[con_id=N] move container to workspace ...`, which Sway applies
reliably for tiled and floating windows.
"""

import os
import shutil
import subprocess
import sys
import threading

from . import apps as appsmod
from . import config, swayipc, ui

APP_ID = "org.luxenos.Overview"


def next_free_workspace(workspaces):
    used = {w["num"] for w in workspaces if w.get("num", -1) >= 0}
    n = 1
    while n in used:
        n += 1
    return n


def workspace_cmd(ws):
    if ws.get("num", -1) >= 0 and str(ws["num"]) == ws["name"]:
        return "workspace number %d" % ws["num"]
    return "workspace %s" % swayipc.quote_name(ws["name"])


def move_cmd(con_id, ws_name=None, number=None):
    crit = swayipc.con_criteria(con_id)
    if number is not None:
        return "%s move container to workspace number %d" % (crit, int(number))
    return "%s move container to workspace %s" % (crit, swayipc.quote_name(ws_name))


def display_name(ws):
    """'2: Mail' -> 'Mail'; '3' -> ''."""
    name = str(ws.get("name", ""))
    num = ws.get("num", -1)
    if num >= 0 and name.startswith("%d:" % num):
        return name.split(":", 1)[1].strip()
    if num >= 0 and name == str(num):
        return ""
    return name


def rename_cmd(ws, label):
    """Keep the workspace number so Super+N still reaches it."""
    label = " ".join(label.split())[:40]
    num = ws.get("num", -1)
    new = ("%d: %s" % (num, label) if label else str(num)) if num >= 0 else (label or ws["name"])
    return "rename workspace %s to %s" % (swayipc.quote_name(ws["name"]), swayipc.quote_name(new))


def close_cmds(ws, workspaces):
    """Move every window of ws to the nearest other workspace (or a new one)."""
    others = [w for w in workspaces if w["name"] != ws["name"]]
    if others:
        target = min(others, key=lambda w: abs(w.get("num", 0) - ws.get("num", 0)))
        dest = "workspace %s" % swayipc.quote_name(target["name"])
    else:
        dest = "workspace number %d" % next_free_workspace(workspaces)
    cmds = ["%s move container to %s" % (swayipc.con_criteria(w["id"]), dest) for w in ws["windows"]]
    cmds.append(dest)
    return cmds


def preview_targets(tree):
    """[(con_id, 'x,y wxh')] of windows visible on screen right now."""
    out = []
    for w, ws, _o in swayipc.iter_windows(tree):
        r = w.get("rect") or {}
        if ws == "__i3_scratch" or not w.get("visible") or r.get("width", 0) < 40 or r.get("height", 0) < 40:
            continue
        out.append((w["id"], "%d,%d %dx%d" % (r["x"], r["y"], r["width"], r["height"])))
    return out


def capture_previews(tree, scale=0.25, timeout=1.5):
    """{con_id: png bytes}. Needs grim; windows are captured in parallel."""
    if not shutil.which("grim") or not os.environ.get("WAYLAND_DISPLAY"):
        return {}
    if not config.get_bool(config.read_kv(os.path.join(config.luxenos_config_dir(), "overview.conf")),
                           "OVERVIEW_PREVIEWS", True):
        return {}
    shots, threads = {}, []

    def grab(con_id, geom):
        try:
            p = subprocess.run(["grim", "-s", str(scale), "-g", geom, "-t", "png", "-l", "1", "-"],
                               capture_output=True, timeout=timeout, check=False)
            if p.returncode == 0 and p.stdout:
                shots[con_id] = p.stdout
        except (OSError, subprocess.SubprocessError):
            pass

    for con_id, geom in preview_targets(tree)[:12]:
        t = threading.Thread(target=grab, args=(con_id, geom), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout + 0.5)
    return shots


def matches(window, entry, query):
    q = appsmod.normalize(query)
    if not q:
        return True
    hay = appsmod.normalize(" ".join(filter(None, [
        window.get("name"), swayipc.window_app_key(window), entry.name if entry else ""])))
    return all(t in hay for t in q.split())


def main(argv=None):
    Gtk = ui.init(layer_shell=True)
    from gi.repository import Gdk, Gio, GLib, GObject, Pango

    class Overview(Gtk.ApplicationWindow):
        def __init__(self, app, tree, outputs, previews=None):
            super().__init__(application=app, title="Workspace Overview")
            self.app = app
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            focused_out = next((o.get("name") for o in outputs if o.get("focused")), None)
            monitor = None
            mons = Gdk.Display.get_default().get_monitors()
            for i in range(mons.get_n_items()):
                m = mons.get_item(i)
                if m.get_connector() == focused_out:
                    monitor = m
            if not ui.make_layer(self, "luxenos-overview", layer="overlay",
                                 anchors=("top", "bottom", "left", "right"),
                                 keyboard="exclusive", exclusive_zone=-1, monitor=monitor):
                self.fullscreen()
            self.tree = tree
            self.previews = previews or {}
            self.outputs = [o for o in outputs if o.get("active", True)]
            self.workspaces = swayipc.workspaces_with_windows(tree)
            self.entries = appsmod.load_apps(include_hidden=True)
            self.index = appsmod.AppIndex(self.entries)

            self.revealer = Gtk.Revealer()
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
            self.revealer.set_transition_duration(ui.transition_ms(160))
            self.set_child(self.revealer)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            root.add_css_class("lx-glass")
            root.add_css_class("lx-overview")
            self.revealer.set_child(root)

            top = Gtk.Box(spacing=12)
            title = ui.label("Workspaces", "lx-title")
            title.set_hexpand(True)
            top.append(title)
            self.search = Gtk.SearchEntry()
            self.search.add_css_class("lx-search")
            self.search.set_placeholder_text("Filter windows")
            self.search.set_size_request(320, -1)
            ui.accessible_label(self.search, "Filter windows by title or application")
            self.search.connect("search-changed", lambda *_: self.populate())
            self.search.connect("activate", lambda *_: self.focus_first())
            self.search.connect("stop-search", lambda *_: self.close())
            top.append(self.search)
            top.append(ui.icon_button("window-close-symbolic", "Close overview", self.close))
            root.append(top)
            root.append(ui.label("Click a window to focus it, drag it to another workspace to move it. "
                                 "Arrow keys move, 1-9 switch workspace, ☰ renames or closes a workspace, "
                                 "Esc closes.", "lx-dim lx-small"))

            scroller = Gtk.ScrolledWindow(vexpand=True)
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            scroller.set_child(self.body)
            root.append(scroller)

            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect("key-pressed", self.on_key)
            self.add_controller(keys)
            self.populate()
            GLib.idle_add(lambda: self.revealer.set_reveal_child(True) or False)

        def on_key(self, _c, keyval, _code, state):
            if keyval == Gdk.KEY_Escape:
                self.close()
                return True
            if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                return False
            if not self.search.get_text() and Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
                self.run_and_close("workspace number %d" % (keyval - Gdk.KEY_0))
                return True
            dirs = {Gdk.KEY_Left: Gtk.DirectionType.LEFT, Gdk.KEY_Right: Gtk.DirectionType.RIGHT,
                    Gdk.KEY_Up: Gtk.DirectionType.UP, Gdk.KEY_Down: Gtk.DirectionType.DOWN}
            if keyval in dirs:
                focus = self.get_focus()
                if focus is self.search or (focus is not None and focus.is_ancestor(self.search)):
                    if keyval != Gdk.KEY_Down:
                        return False
                    first = self.first_tile()
                    if first is not None:
                        first.grab_focus()
                    return True
                self.body.child_focus(dirs[keyval])
                return True
            return False

        def first_tile(self):
            stack = [self.body]
            while stack:
                w = stack.pop(0)
                if w.has_css_class("lx-window-tile"):
                    return w
                c = w.get_first_child()
                while c is not None:
                    stack.append(c)
                    c = c.get_next_sibling()
            return None

        def entry_for(self, window):
            return self.index.lookup(swayipc.window_app_key(window))

        def populate(self):
            child = self.body.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.body.remove(child)
                child = nxt
            query = self.search.get_text()
            outputs = [o.get("name") for o in self.outputs] or [None]
            for out in outputs:
                wss = [w for w in self.workspaces if w["output"] == out or out is None]
                if not wss and out is not None:
                    continue
                if len(outputs) > 1:
                    o = next((x for x in self.outputs if x.get("name") == out), {})
                    desc = " ".join(filter(None, [o.get("make"), o.get("model")])) or out
                    self.body.append(ui.label("%s — %s" % (out, desc), "lx-section"))
                flow = Gtk.FlowBox()
                flow.set_selection_mode(Gtk.SelectionMode.NONE)
                flow.set_homogeneous(False)
                flow.set_max_children_per_line(4)
                flow.set_min_children_per_line(1)
                flow.set_column_spacing(14)
                flow.set_row_spacing(14)
                for ws in wss:
                    flow.append(self.make_card(ws, query))
                if out == outputs[-1] or out is None:
                    flow.append(self.make_new_card())
                self.body.append(flow)

        def make_card(self, ws, query):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("lx-ws-card")
            if ws["focused"]:
                card.add_css_class("focused")
            head = Gtk.Button()
            head.add_css_class("flat")
            head.set_hexpand(True)
            n = len(ws["windows"])
            label = display_name(ws)
            title = ("%d · %s" % (ws["num"], label) if ws.get("num", -1) >= 0 else label) if label \
                else "Workspace %s" % ws["name"]
            head_label = ui.label("%s  ·  %d window%s" % (title, n, "" if n == 1 else "s"), "lx-heading")
            head.set_child(head_label)
            ui.accessible_label(head, "Switch to %s, %d windows" % (title, n))
            head.connect("clicked", lambda *_: self.run_and_close(workspace_cmd(ws)))
            top = Gtk.Box(spacing=4)
            top.append(head)
            menu = Gtk.MenuButton(icon_name="open-menu-symbolic")
            menu.add_css_class("flat")
            ui.accessible_label(menu, "Workspace %s options" % ws["name"])
            menu.set_popover(self.workspace_menu(ws))
            top.append(menu)
            card.append(top)
            shown = 0
            for w in ws["windows"]:
                entry = self.entry_for(w)
                if not matches(w, entry, query):
                    continue
                card.append(self.make_tile(w, entry))
                shown += 1
            if not ws["windows"]:
                card.append(ui.label("Empty", "lx-dim"))
            elif shown == 0:
                card.append(ui.label("No matching windows", "lx-dim"))
            drop = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
            drop.connect("enter", lambda *_: (card.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
            drop.connect("leave", lambda *_: card.remove_css_class("drop-target"))
            drop.connect("drop", lambda _t, value, _x, _y: self.drop_on(value, ws))
            card.add_controller(drop)
            return card

        def make_new_card(self):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("lx-ws-card")
            n = next_free_workspace(self.workspaces)
            btn = Gtk.Button(label="New workspace (%d)" % n)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: self.run_and_close("workspace number %d" % n))
            card.append(btn)
            card.append(ui.label("Drop a window here to move it to a new workspace.", "lx-dim lx-small", wrap=True))
            drop = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
            drop.connect("enter", lambda *_: (card.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
            drop.connect("leave", lambda *_: card.remove_css_class("drop-target"))
            drop.connect("drop", lambda _t, value, _x, _y: self.move(value, number=n))
            card.add_controller(drop)
            return card

        def make_tile(self, w, entry):
            btn = Gtk.Button()
            btn.add_css_class("lx-window-tile")
            if w.get("focused"):
                btn.add_css_class("focused")
            box = Gtk.Box(spacing=10)
            gicon = entry.info.get_icon() if entry is not None else None
            shot = self.previews.get(w.get("id"))
            if shot:
                try:
                    tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(shot))
                    pic = Gtk.Picture.new_for_paintable(tex)
                    pic.set_can_shrink(True)
                    pic.set_size_request(160, 100)
                    pic.add_css_class("lx-preview")
                    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    outer.append(pic)
                    outer.append(box)
                    box_parent = outer
                except GLib.Error:
                    box_parent = box
            else:
                box_parent = box
            box.append(ui.gicon_image(gicon, 32))
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = w.get("name") or "(untitled)"
            t = ui.label(title)
            t.set_ellipsize(Pango.EllipsizeMode.END)
            t.set_max_width_chars(28)
            vb.append(t)
            app_name = entry.name if entry is not None else (swayipc.window_app_key(w) or "Unknown app")
            extra = " · floating" if w.get("type") == "floating_con" else ""
            vb.append(ui.label(app_name + extra, "lx-dim lx-small"))
            box.append(vb)
            btn.set_child(box_parent)
            ui.accessible_label(btn, "%s, %s. Enter to focus, Menu key for more." % (title, app_name))
            btn.connect("clicked", lambda *_: self.run_and_close("%s focus" % swayipc.con_criteria(w["id"])))
            drag = Gtk.DragSource()
            drag.set_actions(Gdk.DragAction.MOVE)
            drag.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(int(w["id"])))
            btn.add_controller(drag)
            click = Gtk.GestureClick(button=3)
            click.connect("pressed", lambda *_: self.window_menu(btn, w))
            btn.add_controller(click)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _c, kv, _k, st: (
                self.window_menu(btn, w) or True) if kv == Gdk.KEY_Menu or
                (kv == Gdk.KEY_F10 and st & Gdk.ModifierType.SHIFT_MASK) else False)
            btn.add_controller(keys)
            return btn

        def workspace_menu(self, ws):
            pop = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(6)
            box.append(ui.label("Name", "lx-small lx-dim"))
            entry = Gtk.Entry(text=display_name(ws), placeholder_text="e.g. Mail", max_length=40)
            ui.accessible_label(entry, "Workspace name")

            def rename(*_):
                pop.popdown()
                self.run_sway(rename_cmd(ws, entry.get_text()))
                self.reload()

            entry.connect("activate", rename)
            box.append(entry)
            ok = Gtk.Button(label="Rename")
            ok.connect("clicked", rename)
            box.append(ok)
            box.append(Gtk.Separator())
            num = ws.get("num", -1)
            if num >= 0 and shutil.which("luxenos-wallpaper"):
                wp = Gtk.Button(label="Wallpaper for this workspace…")
                wp.add_css_class("flat")
                wp.connect("clicked", lambda *_: (pop.popdown(), self.pick_wallpaper(num)))
                box.append(wp)
                rm = Gtk.Button(label="Use the normal wallpaper")
                rm.add_css_class("flat")
                rm.connect("clicked", lambda *_: (pop.popdown(),
                                                  ui.spawn(["luxenos-wallpaper", "unset", "--workspace", str(num)])))
                box.append(rm)
            close = Gtk.Button(label="Close workspace (keep its windows)")
            close.add_css_class("flat")
            close.connect("clicked", lambda *_: (pop.popdown(), self.close_workspace(ws)))
            box.append(close)
            pop.set_child(box)
            return pop

        def close_workspace(self, ws):
            for cmd in close_cmds(ws, self.workspaces):
                self.run_sway(cmd)
            self.reload()

        def pick_wallpaper(self, num):
            dialog = Gtk.FileDialog(title="Wallpaper for workspace %d" % num)
            filt = Gtk.FileFilter()
            filt.set_name("Images")
            for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
                filt.add_pattern(pat)
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dialog.set_filters(store)

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is not None and f.get_path():
                    ui.spawn(["luxenos-wallpaper", "set", f.get_path(), "--workspace", str(num)])

            dialog.open(self, None, done)

        def window_menu(self, btn, w):
            pop = Gtk.Popover()
            pop.set_parent(btn)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(ui.label("Move to…", "lx-heading"))

            def add(text, cmd):
                b = Gtk.Button(label=text)
                b.add_css_class("flat")
                b.get_child().set_xalign(0)
                b.connect("clicked", lambda *_: (pop.popdown(), self.run_sway(cmd), self.reload()))
                box.append(b)

            for ws in self.workspaces:
                if any(x.get("id") == w["id"] for x in ws["windows"]):
                    continue
                add("Workspace %s" % ws["name"], move_cmd(w["id"], ws_name=ws["name"]))
            n = next_free_workspace(self.workspaces)
            add("New workspace (%d)" % n, move_cmd(w["id"], number=n))
            box.append(Gtk.Separator())
            add("Close window", "%s kill" % swayipc.con_criteria(w["id"]))
            pop.set_child(box)
            pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
            pop.popup()

        def drop_on(self, con_id, ws):
            for x in ws["windows"]:
                if x.get("id") == con_id:
                    return False
            return self.move(con_id, ws_name=ws["name"])

        def move(self, con_id, ws_name=None, number=None):
            ok = self.run_sway(move_cmd(con_id, ws_name=ws_name, number=number))
            self.reload()
            return ok

        def run_sway(self, cmd):
            try:
                swayipc.command(cmd)
                return True
            except swayipc.IPCError as e:
                print("luxenos-overview: %s" % e, file=sys.stderr)
                return False

        def reload(self):
            try:
                with swayipc.Connection() as c:
                    self.tree = c.get_tree()
            except swayipc.IPCError:
                return
            self.workspaces = swayipc.workspaces_with_windows(self.tree)
            self.populate()

        def focus_first(self):
            query = self.search.get_text()
            for ws in self.workspaces:
                for w in ws["windows"]:
                    if matches(w, self.entry_for(w), query):
                        self.run_and_close("%s focus" % swayipc.con_criteria(w["id"]))
                        return

        def run_and_close(self, cmd):
            self.close()
            GLib.idle_add(lambda: self.run_sway(cmd) and False)

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
            try:
                with swayipc.Connection() as c:
                    tree, outputs = c.get_tree(), c.get_outputs()
            except swayipc.IPCError as e:
                print("luxenos-overview: %s" % e, file=sys.stderr)
                return 1
            # Before the overlay covers the screen.
            previews = capture_previews(tree)
            self.window = Overview(self, tree, outputs, previews)
            self.window.present()
            self.window.search.grab_focus()
            return 0

    return App().run(argv if argv is not None else sys.argv)
