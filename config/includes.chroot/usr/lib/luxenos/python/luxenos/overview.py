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

Moves use `[con_id=N] move container to workspace ...`, which Sway applies
reliably for tiled and floating windows.
"""

import sys

from . import apps as appsmod
from . import swayipc, ui

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
        def __init__(self, app, tree, outputs):
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
                                 "1-9 switch workspace, Esc closes.", "lx-dim lx-small"))

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
            return False

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
            n = len(ws["windows"])
            head_label = ui.label("Workspace %s  ·  %d window%s" % (ws["name"], n, "" if n == 1 else "s"),
                                  "lx-heading")
            head.set_child(head_label)
            ui.accessible_label(head, "Switch to workspace %s, %d windows" % (ws["name"], n))
            head.connect("clicked", lambda *_: self.run_and_close(workspace_cmd(ws)))
            card.append(head)
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
            btn.set_child(box)
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
            self.window = Overview(self, tree, outputs)
            self.window.present()
            self.window.search.grab_focus()
            return 0

    return App().run(argv if argv is not None else sys.argv)
