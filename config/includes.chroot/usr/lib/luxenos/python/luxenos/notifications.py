"""Notification Center (Super+Shift+N): history, per-app settings, sounds.

Built on dunst, the notification daemon LuxenOS already runs; it is not a
second daemon. Everything goes through dunst's own interfaces:

  * history, grouped by application: `dunstctl history` (JSON); "Show again"
    re-displays a notification with its actions (`dunstctl history-pop ID`),
    one or all can be removed (`history-rm`, `history-clear`)
  * Do Not Disturb: `dunstctl set-paused` (same switch as Quick Settings)
  * actions: dunst shows a notification's buttons through its action menu
    (wofi), `dunstctl context`, also on Ctrl+Shift+period
  * per-application rules and sounds: a dunst drop-in this module generates,
    ~/.config/dunst/dunstrc.d/50-luxenos-notifications.conf, then
    `dunstctl reload` (the glass dunstrc itself stays owned by luxenos-glass)

Command line: luxenos-notifications [--write-config]
  --write-config  only (re)write the dunst drop-in (run at login)

Settings: ~/.config/luxenos/notifications.conf
  NOTIFY_SOUND=yes|no                  play a sound for new notifications
  NOTIFY_SOUND_FILE=/usr/share/sounds/freedesktop/stereo/message.oga
  NOTIFY_SOUND_CRITICAL=/usr/share/sounds/freedesktop/stereo/dialog-warning.oga
  NOTIFY_HISTORY=100                   notifications kept in history (10-500)
  APP_<KEY>_NAME=Firefox               <KEY>: app name upper-cased, [A-Z0-9_]
  APP_<KEY>_MODE=allow|silent|block    silent: history only, no popup;
                                       block: dropped entirely
  APP_<KEY>_SOUND=yes|no
  APP_<KEY>_URGENCY=default|low|normal|critical
"""

import json
import os
import re
import shutil
import subprocess
import sys

from . import config, ui

APP_ID = "org.luxenos.Notifications"
MODES = ("allow", "silent", "block")
URGENCIES = ("default", "low", "normal", "critical")
DEFAULT_SOUND = "/usr/share/sounds/freedesktop/stereo/message.oga"
DEFAULT_CRITICAL = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"
SOUND_SCRIPT = "/usr/local/bin/luxenos-notify-sound"


def conf_path():
    return os.path.join(config.luxenos_config_dir(), "notifications.conf")


def dropin_path():
    return os.path.join(config.config_home(), "dunst", "dunstrc.d", "50-luxenos-notifications.conf")


def app_key(name):
    key = re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")[:40]
    return key or "UNKNOWN"


def load():
    return config.read_kv(conf_path())


def app_settings(values):
    """{key: {'name','mode','sound','urgency'}} for every configured app."""
    apps = {}
    for k, v in values.items():
        m = re.match(r"^APP_([A-Z0-9_]+)_NAME$", k)
        if m:
            key = m.group(1)
            apps[key] = {"name": v,
                         "mode": config.get_choice(values, "APP_%s_MODE" % key, "allow", MODES),
                         "sound": config.get_bool(values, "APP_%s_SOUND" % key, True),
                         "urgency": config.get_choice(values, "APP_%s_URGENCY" % key, "default", URGENCIES)}
    return apps


def _dunst_string(s):
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')


def dropin_text(values):
    """The dunst drop-in for these settings (rules match on the exact app name)."""
    lines = ["# Managed by luxenos-notifications (Notification Center). Changes are overwritten.",
             "[global]",
             "    history_length = %d" % config.get_int(values, "NOTIFY_HISTORY", 100, 10, 500),
             "    sticky_history = yes",
             '    dmenu = "/usr/bin/wofi --dmenu --prompt Actions"',
             "    mouse_right_click = context, close_current", ""]
    if config.get_bool(values, "NOTIFY_SOUND", True) and os.path.exists(SOUND_SCRIPT):
        lines += ["[luxenos-sound]", '    appname = "*"', "    script = %s" % SOUND_SCRIPT, ""]
    for key, a in sorted(app_settings(values).items()):
        if a["mode"] == "allow" and a["urgency"] == "default":
            continue
        name = a["name"]
        if any(c in name for c in "\n\r[]") or not name.strip():
            continue
        lines += ["[luxenos-app-%s]" % key.lower(), "    appname = %s" % _dunst_string(name)]
        if a["mode"] in ("silent", "block"):
            lines.append("    skip_display = yes")
        if a["mode"] == "block":
            lines.append("    history_ignore = yes")
        if a["urgency"] != "default":
            lines.append("    set_urgency = %s" % a["urgency"])
        lines.append("")
    return "\n".join(lines) + "\n"


def write_dropin(values=None):
    values = load() if values is None else values
    path = dropin_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dropin_text(values))
    os.replace(tmp, path)
    return reload_dunst()


def reload_dunst():
    if not shutil.which("dunstctl"):
        return False
    try:
        return subprocess.run(["dunstctl", "reload"], capture_output=True, timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def sound_for(values, appname, urgency, paused=False):
    """Sound file to play for one notification, or None (used by luxenos-notify-sound)."""
    if paused or not config.get_bool(values, "NOTIFY_SOUND", True):
        return None
    a = app_settings(values).get(app_key(appname))
    if a and (a["mode"] != "allow" or not a["sound"]):
        return None
    key = "NOTIFY_SOUND_CRITICAL" if urgency.upper() == "CRITICAL" else "NOTIFY_SOUND_FILE"
    path = values.get(key) or (DEFAULT_CRITICAL if key == "NOTIFY_SOUND_CRITICAL" else DEFAULT_SOUND)
    return path if os.path.isfile(path) else None


# --- dunst history -------------------------------------------------------------------

def _val(entry, key, default=""):
    v = entry.get(key)
    if isinstance(v, dict):
        return v.get("data", default)
    return default if v is None else v


def parse_history(text):
    """`dunstctl history` JSON -> [{'id','app','summary','body','time','urgency','icon'}], newest first."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    raw = data.get("data") or []
    if raw and isinstance(raw[0], list):
        raw = raw[0]
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        try:
            nid = int(_val(e, "id", 0))
        except (TypeError, ValueError):
            continue
        out.append({"id": nid, "app": str(_val(e, "appname")) or "Unknown",
                    "summary": str(_val(e, "summary")), "body": str(_val(e, "body")),
                    "time": _val(e, "timestamp", 0), "urgency": str(_val(e, "urgency", "NORMAL")),
                    "icon": str(_val(e, "icon_path"))})
    out.sort(key=lambda n: -n["id"])
    return out


def group(history):
    """[(app, [notifications])] with the most recently active app first."""
    groups = {}
    order = []
    for n in history:
        if n["app"] not in groups:
            groups[n["app"]] = []
            order.append(n["app"])
        groups[n["app"]].append(n)
    return [(a, groups[a]) for a in order]


def dunstctl(*args, timeout=5):
    if not shutil.which("dunstctl"):
        raise RuntimeError("dunst is not installed")
    p = subprocess.run(["dunstctl"] + list(args), capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip() or "dunstctl %s failed" % args[0])
    return p.stdout


def strip_markup(text):
    return re.sub(r"<[^>]+>", "", text or "")


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    if "--write-config" in argv[1:]:
        # luxenos-session-init: the dunst drop-in must exist before dunst starts.
        write_dropin()
        return 0
    Gtk = ui.init(layer_shell=True)
    from gi.repository import Gio, GLib, Pango

    class Panel(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Notifications")
            self.add_css_class("lx-layer")
            self.set_decorated(False)
            self.set_default_size(400, 640)
            if not ui.make_layer(self, "luxenos-notifications", layer="overlay", anchors=("top", "right", "bottom"),
                                 margins={"top": 48, "right": 8, "bottom": 8}, keyboard="on_demand"):
                self.set_default_size(420, 640)
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            root.add_css_class("lx-glass")
            root.set_size_request(380, -1)
            self.set_child(root)
            top = Gtk.Box(spacing=8)
            t = ui.label("Notifications", "lx-title")
            t.set_hexpand(True)
            top.append(t)
            self.stack = Gtk.Stack()
            sw = Gtk.StackSwitcher(stack=self.stack)
            top.append(sw)
            top.append(ui.icon_button("window-close-symbolic", "Close", self.close))
            root.append(top)
            dnd = Gtk.Box(spacing=8)
            dnd.append(ui.label("Do Not Disturb", None))
            self.dnd = Gtk.Switch(halign=Gtk.Align.END, hexpand=True)
            ui.accessible_label(self.dnd, "Do Not Disturb")
            dnd.append(self.dnd)
            root.append(dnd)
            self.msg = ui.label("", "lx-small lx-dim", wrap=True)
            root.append(self.msg)
            root.append(self.stack)
            self.stack.set_vexpand(True)
            self.build_history()
            self.build_apps()
            ui.add_escape_handler(self, self.close)
            try:
                self.dnd.set_active(dunstctl("is-paused").strip() == "true")
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                self.msg.set_text("Notifications are unavailable: %s" % e)
                self.dnd.set_sensitive(False)
            self.dnd.connect("notify::active", lambda *_: self.set_dnd())
            self.refresh()
            GLib.timeout_add_seconds(3, lambda: self.get_visible() and (self.refresh() or True))

        def build_history(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            row = Gtk.Box(spacing=6)
            act = Gtk.Button(label="Actions of newest")
            ui.accessible_label(act, "Show the action buttons of the newest notification on screen")
            act.connect("clicked", lambda *_: self.run_quiet("context"))
            clear = Gtk.Button(label="Clear all", hexpand=True, halign=Gtk.Align.END)
            clear.connect("clicked", lambda *_: self.clear_all())
            row.append(act)
            row.append(clear)
            box.append(row)
            self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(self.list)
            box.append(sw)
            self.stack.add_titled(box, "history", "History")

        def build_apps(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            values = load()
            snd = Gtk.Box(spacing=8)
            snd.append(ui.label("Notification sounds", None))
            self.sound = Gtk.Switch(active=config.get_bool(values, "NOTIFY_SOUND", True), halign=Gtk.Align.END,
                                    hexpand=True)
            ui.accessible_label(self.sound, "Notification sounds")
            self.sound.connect("notify::active", lambda *_: self.save({"NOTIFY_SOUND": "yes" if
                                                                      self.sound.get_active() else "no"}))
            snd.append(self.sound)
            box.append(snd)
            box.append(ui.label("Per application. Silent: kept in history without a popup. Blocked: dropped.",
                                "lx-dim lx-small", wrap=True))
            self.apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(self.apps_box)
            box.append(sw)
            self.stack.add_titled(box, "apps", "Apps")

        def refresh(self):
            try:
                history = parse_history(dunstctl("history"))
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                history = []
                self.msg.set_text("History unavailable: %s" % e)
            ids = tuple(n["id"] for n in history)
            if ids == getattr(self, "shown_ids", None):
                return
            self.shown_ids = ids
            self.history = history
            while (c := self.list.get_first_child()) is not None:
                self.list.remove(c)
            if not history:
                self.list.append(ui.label("No notifications.", "lx-dim"))
            for app, items in group(history):
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                card.add_css_class("lx-card")
                head = Gtk.Box(spacing=6)
                h = ui.label("%s (%d)" % (app, len(items)), "lx-heading")
                h.set_hexpand(True)
                head.append(h)
                head.append(ui.icon_button("edit-clear-symbolic", "Remove %s notifications" % app,
                                           lambda its=items: self.remove(its)))
                card.append(head)
                for n in items[:5]:
                    btn = Gtk.Button()
                    btn.add_css_class("flat")
                    vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                    s = ui.label(strip_markup(n["summary"]) or "(no title)", "lx-crit" if n["urgency"] == "CRITICAL"
                                 else None)
                    s.set_ellipsize(Pango.EllipsizeMode.END)
                    vb.append(s)
                    if n["body"]:
                        b = ui.label(strip_markup(n["body"]), "lx-dim lx-small", wrap=True)
                        b.set_lines(3)
                        b.set_ellipsize(Pango.EllipsizeMode.END)
                        vb.append(b)
                    btn.set_child(vb)
                    ui.accessible_label(btn, "%s: %s. Show again" % (app, strip_markup(n["summary"])))
                    btn.connect("clicked", lambda _b, i=n["id"]: self.run_quiet("history-pop", str(i)))
                    card.append(btn)
                if len(items) > 5:
                    card.append(ui.label("and %d older" % (len(items) - 5), "lx-dim lx-small"))
                self.list.append(card)
            self.fill_apps()

        def fill_apps(self):
            values = load()
            configured = app_settings(values)
            names = {a["name"] for a in configured.values()}
            for n in getattr(self, "history", []):
                names.add(n["app"])
            while (c := self.apps_box.get_first_child()) is not None:
                self.apps_box.remove(c)
            for name in sorted(names, key=str.lower):
                key = app_key(name)
                a = configured.get(key, {"mode": "allow", "sound": True, "urgency": "default"})
                row = Gtk.Box(spacing=6)
                lbl = ui.label(name, None)
                lbl.set_hexpand(True)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                row.append(lbl)
                mode = Gtk.DropDown.new_from_strings(["Allow", "Silent", "Blocked"])
                mode.set_selected(MODES.index(a["mode"]))
                ui.accessible_label(mode, "Notifications from %s" % name)
                sound = Gtk.CheckButton(label="Sound", active=a["sound"])
                mode.connect("notify::selected", lambda d, n=name, k=key: self.save(
                    {"APP_%s_NAME" % k: n, "APP_%s_MODE" % k: MODES[d.get_selected()]}))
                sound.connect("toggled", lambda c, n=name, k=key: self.save(
                    {"APP_%s_NAME" % k: n, "APP_%s_SOUND" % k: "yes" if c.get_active() else "no"}))
                row.append(mode)
                row.append(sound)
                self.apps_box.append(row)

        def save(self, changes):
            config.update_kv(conf_path(), changes, header="# LuxenOS Notification Center settings.")
            if write_dropin():
                self.msg.set_text("Saved.")
            else:
                self.msg.set_text("Saved; dunst could not reload, the change applies after the next login.")

        def set_dnd(self):
            self.run_quiet("set-paused", "true" if self.dnd.get_active() else "false")

        def remove(self, items):
            for n in items:
                try:
                    dunstctl("history-rm", str(n["id"]))
                except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                    self.msg.set_text(str(e))
                    break
            self.shown_ids = None
            self.refresh()

        def clear_all(self):
            self.run_quiet("history-clear")
            self.shown_ids = None
            self.refresh()

        def run_quiet(self, *args):
            try:
                dunstctl(*args)
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                self.msg.set_text(str(e))

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            w = self.get_active_window()
            if w is not None:
                w.close()
                return
            if not os.path.exists(dropin_path()):
                write_dropin()
            Panel(self).present()

    return App().run(argv[:1])


def sound_main():
    """luxenos-notify-sound: run by dunst for each notification (rule script)."""
    values = load()
    paused = False
    if shutil.which("dunstctl"):
        try:
            paused = subprocess.run(["dunstctl", "is-paused"], capture_output=True, text=True, timeout=2,
                                    check=False).stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            pass
    # dunst passes appname summary body icon urgency as arguments (and DUNST_* variables).
    app = os.environ.get("DUNST_APP_NAME") or (sys.argv[1] if len(sys.argv) > 1 else "")
    urgency = os.environ.get("DUNST_URGENCY") or (sys.argv[5] if len(sys.argv) > 5 else "NORMAL")
    path = sound_for(values, app, urgency, paused)
    if not path:
        return 0
    for player in (["pw-play", path], ["paplay", path]):
        if shutil.which(player[0]):
            subprocess.Popen(player, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return 0
    return 0

