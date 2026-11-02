"""Wine Manager: Windows-program environments (prefixes) per application.

  * prefixes live in ~/.local/share/luxenos/wine/prefixes/NAME (the default
    prefix, ~/.wine or LUXENOS_WINE_PREFIX, is listed as "default");
  * a program can be assigned to a prefix (~/.config/luxenos/wine-apps.list,
    "ABSOLUTE-PATH<TAB>NAME"), which luxenos-wine-open - the existing .exe
    double-click handler - honours, so double-clicking keeps working and now
    uses the right prefix;
  * launcher shortcuts: ~/.local/share/applications/luxenos-wine-*.desktop,
    which run `luxenos-wine-open --prefix NAME PROGRAM` (same checks as a
    double-click);
  * compatibility: the Windows version a prefix reports (winecfg -v), or
    the full winecfg;
  * backup: a .tar.gz of a prefix in ~/WineBackups; restore unpacks it as a
    new prefix after checking every entry.

Everything runs as you; Wine is never run as root.
"""

import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time

from . import config, ui

APP_ID = "org.luxenos.WineManager"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
WINVERS = (("win11", "Windows 11"), ("win10", "Windows 10"), ("win81", "Windows 8.1"), ("win7", "Windows 7"),
           ("winxp", "Windows XP"))


def data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def prefixes_dir():
    return os.path.join(data_home(), "luxenos", "wine", "prefixes")


def apps_list_path():
    return os.path.join(config.luxenos_config_dir(), "wine-apps.list")


def backups_dir():
    return os.path.join(os.path.expanduser("~"), "WineBackups")


def default_prefix():
    values = config.read_layered("/etc/luxenos/wine.conf", os.path.join(config.luxenos_config_dir(), "wine.conf"))
    p = values.get("LUXENOS_WINE_PREFIX") or os.path.join(os.path.expanduser("~"), ".wine")
    return os.path.expanduser(p)


def prefix_path(name):
    if name == "default":
        return default_prefix()
    if not NAME_RE.match(name or ""):
        raise ValueError("prefix names use letters, digits, dot, dash and underscore")
    return os.path.join(prefixes_dir(), name)


def list_prefixes():
    out = [("default", default_prefix())]
    try:
        for n in sorted(os.listdir(prefixes_dir()), key=str.lower):
            d = os.path.join(prefixes_dir(), n)
            if NAME_RE.match(n) and os.path.isdir(d) and not os.path.islink(d):
                out.append((n, d))
    except OSError:
        pass
    return out


def prefix_info(path):
    """{'ready', 'arch', 'size'} without starting Wine."""
    info = {"ready": os.path.isfile(os.path.join(path, "system.reg")), "arch": "", "size": None}
    try:
        with open(os.path.join(path, "system.reg"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("#arch="):
                    info["arch"] = line.strip()[6:]
                    break
    except OSError:
        pass
    return info


# --- assignments --------------------------------------------------------------------

def parse_apps_list(text):
    out = {}
    for line in text.splitlines():
        if "\t" not in line or line.startswith("#"):
            continue
        path, name = line.split("\t", 1)
        if path.startswith("/") and NAME_RE.match(name.strip()):
            out[path] = name.strip()
    return out


def load_assignments():
    try:
        with open(apps_list_path(), encoding="utf-8") as f:
            return parse_apps_list(f.read())
    except OSError:
        return {}


def save_assignments(mapping):
    path = apps_list_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Windows program -> Wine prefix (luxenos-wine-manager). Read by luxenos-wine-open."]
    for p, n in sorted(mapping.items()):
        if "\t" in p or "\n" in p or not NAME_RE.match(n):
            raise ValueError("unsupported path or prefix name")
        lines.append("%s\t%s" % (p, n))
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)


def assign(program, name):
    mapping = load_assignments()
    program = os.path.realpath(program)
    if name in (None, "", "default"):
        mapping.pop(program, None)
    else:
        prefix_path(name)
        mapping[program] = name
    save_assignments(mapping)


# --- shortcuts -----------------------------------------------------------------------

def desktop_quote(arg):
    """Quote one Exec argument per the Desktop Entry spec."""
    if "\n" in arg:
        raise ValueError("newline in path")
    inner = re.sub(r'(["`$\\])', r"\\\1", arg)
    # The string-level escape rule applies first, so every backslash doubles.
    return '"%s"' % inner.replace("\\", "\\\\").replace("%", "%%")


def shortcut_text(title, program, name):
    clean = " ".join(title.replace("\n", " ").split())[:80] or os.path.basename(program)
    return ("[Desktop Entry]\nType=Application\nName=%s\nComment=Windows program (Wine, prefix %s)\n"
            "Exec=luxenos-wine-open --prefix %s %s\nIcon=wine\nCategories=Utility;X-Wine;\nTerminal=false\n"
            "X-LuxenOS-Wine-Prefix=%s\n" % (clean, name, name, desktop_quote(program), name))


def create_shortcut(title, program, name):
    prefix_path(name)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "program"
    path = os.path.join(data_home(), "applications", "luxenos-wine-%s-%s.desktop" % (name.lower(), slug))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(shortcut_text(title, program, name))
    os.replace(path + ".tmp", path)
    return path


# --- backup / restore ------------------------------------------------------------------

def backup(name):
    src = prefix_path(name)
    if not os.path.isdir(src):
        raise ValueError("the prefix %s does not exist" % name)
    os.makedirs(backups_dir(), exist_ok=True)
    dest = os.path.join(backups_dir(), "wine-%s-%s.tar.gz" % (name, time.strftime("%Y%m%d-%H%M%S")))
    def keep(member):
        # Extra drive letters (CD-ROM, mapped folders) point outside the
        # prefix: they are left out, so a backup can always be restored.
        parts = member.name.split("/")
        if member.issym() and len(parts) == 3 and parts[1] == "dosdevices" and parts[2] not in ("c:", "z:"):
            return None
        return member

    with tarfile.open(dest + ".part", "w:gz") as t:
        t.add(src, arcname="prefix", filter=keep)
    os.replace(dest + ".part", dest)
    return dest


def check_backup(path):
    """Refuse anything that is not a prefix backup made by this tool."""
    with tarfile.open(path) as t:
        for m in t:
            parts = m.name.split("/")
            if parts[0] != "prefix" or ".." in parts or m.name.startswith("/"):
                raise ValueError("not a LuxenOS Wine backup (unexpected path %r)" % m.name)
            if m.issym() or m.islnk():
                # Wine's drive letters (dosdevices/c:, z:) are the only links allowed.
                if len(parts) != 3 or parts[1] != "dosdevices" or m.islnk():
                    raise ValueError("backup contains an unexpected link: %s" % m.name)
                if parts[2] == "z:":
                    if m.linkname != "/":
                        raise ValueError("backup contains an unexpected z: drive: %s" % m.linkname)
                elif m.linkname.startswith("/") or ".." in m.linkname.split("/")[1:]:
                    raise ValueError("backup contains a link out of the prefix: %s" % m.name)
            elif not (m.isfile() or m.isdir()):
                raise ValueError("backup contains a special file: %s" % m.name)


def restore(path, name):
    dest = prefix_path(name)
    if os.path.exists(dest):
        raise ValueError("a prefix named %s already exists" % name)
    check_backup(path)
    work = dest + ".restoring"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        with tarfile.open(path) as t:
            if hasattr(tarfile, "fully_trusted_filter"):
                t.extractall(work, filter="fully_trusted")  # entries were checked above
            else:
                t.extractall(work)  # noqa: S202 (checked by check_backup)
        os.rename(os.path.join(work, "prefix"), dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


def wine_version():
    if not shutil.which("wine"):
        return None
    try:
        return subprocess.run(["wine", "--version"], capture_output=True, text=True, timeout=20,
                              check=False).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def wine_env(prefix):
    env = dict(os.environ, WINEPREFIX=prefix, WINEDEBUG="-all")
    return env


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Windows Programs")
            self.set_default_size(900, 600)
            self.busy = False
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            new = Gtk.Button(label="New prefix…")
            new.add_css_class("suggested-action")
            new.connect("clicked", lambda *_: self.ask_name("New prefix", "Create", self.create))
            header.pack_start(new)
            rest = Gtk.Button(label="Restore backup…")
            rest.connect("clicked", lambda *_: self.pick_backup())
            header.pack_end(rest)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(14)
            self.set_child(vb)
            self.version = ui.label("", "lx-dim")
            vb.append(self.version)
            vb.append(ui.label("A prefix is a separate Windows environment (its own C: drive and settings). Give "
                               "programs that conflict their own prefix. Windows programs are not sandboxed: they "
                               "can read your files.", "lx-dim lx-small", wrap=True))
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class("boxed-list")
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(self.list)
            vb.append(sw)
            vb.append(ui.label("Programs with their own prefix", "lx-heading"))
            self.assigned = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.assigned.add_css_class("boxed-list")
            vb.append(self.assigned)
            self.status = ui.label("", "lx-small", wrap=True)
            vb.append(self.status)
            threading.Thread(target=lambda: GLib.idle_add(self.show_version, wine_version()), daemon=True).start()
            self.reload()

        def show_version(self, v):
            if v:
                self.version.set_text("Installed: %s" % v)
            else:
                self.version.set_text("Wine is not installed, so Windows programs cannot run on this system.")
            return False

        def reload(self):
            self.list.remove_all()
            for name, path in list_prefixes():
                self.list.append(self.prefix_row(name, path))
            self.assigned.remove_all()
            mapping = load_assignments()
            if not mapping:
                self.assigned.append(ui.label("None: every program uses the default prefix.", "lx-dim"))
            for program, name in sorted(mapping.items()):
                r = Gtk.Box(spacing=6)
                for m in ("start", "end", "top", "bottom"):
                    getattr(r, "set_margin_" + m)(4)
                lbl = ui.label("%s → %s" % (program, name), "lx-small", selectable=True)
                lbl.set_hexpand(True)
                lbl.set_ellipsize(1)
                r.append(lbl)
                b = Gtk.Button(label="Use default")
                b.connect("clicked", lambda _b, p=program: (assign(p, None), self.reload()))
                r.append(b)
                self.assigned.append(r)

        def prefix_row(self, name, path):
            info = prefix_info(path)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(8)
            box.append(ui.label("%s%s" % (name, "" if info["ready"] else " (not set up yet)"), "lx-heading"))
            box.append(ui.label("%s%s" % (path, " · " + info["arch"] if info["arch"] else ""), "lx-dim lx-small",
                                selectable=True))
            row = Gtk.Box(spacing=6)

            def btn(label, cb):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_: cb())
                row.append(b)
                return b

            btn("Run program…", lambda: self.pick_program(name, assign_it=False))
            btn("Assign program…", lambda: self.pick_program(name, assign_it=True))
            ver = Gtk.DropDown.new_from_strings([t for _v, t in WINVERS])
            ui.accessible_label(ver, "Windows version reported by %s" % name)
            ver.set_selected(1)
            row.append(ver)
            btn("Set version", lambda: self.wine(name, ["winecfg", "-v", WINVERS[ver.get_selected()][0]],
                                                 "Windows version set"))
            btn("Wine settings", lambda: self.wine(name, ["winecfg"], "Wine settings closed"))
            btn("Back up", lambda: self.in_thread(lambda: backup(name), "Backup saved: %s"))
            if name != "default":
                d = btn("Delete…", lambda: ui.alert(
                    self, "Delete the prefix %s?" % name, "Its Windows programs and their settings in %s are "
                    "deleted. Make a backup first if you may need them." % path, buttons=("Cancel", "Delete"),
                    default=0, cancel=0, callback=lambda i: i == 1 and self.delete(name)))
                d.add_css_class("destructive-action")
            box.append(row)
            return box

        # -- actions --
        def ask_name(self, title, ok_label, cb):
            dialog = Gtk.Window(title=title, modal=True, transient_for=self)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(16)
            entry = Gtk.Entry(placeholder_text="e.g. office or games", max_length=40)
            ui.accessible_label(entry, "Prefix name")
            box.append(entry)
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            cancel = Gtk.Button(label="Cancel")
            cancel.connect("clicked", lambda *_: dialog.close())
            ok = Gtk.Button(label=ok_label)
            ok.add_css_class("suggested-action")

            def go(*_):
                name = entry.get_text().strip()
                if not NAME_RE.match(name) or name == "default":
                    entry.add_css_class("error")
                    return
                dialog.close()
                cb(name)
            ok.connect("clicked", go)
            entry.connect("activate", go)
            row.append(cancel)
            row.append(ok)
            box.append(row)
            dialog.set_child(box)
            dialog.present()

        def create(self, name):
            path = prefix_path(name)
            if os.path.exists(path):
                ui.alert(self, "Already exists", "A prefix named %s exists." % name)
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            env = dict(wine_env(path), WINEDLLOVERRIDES="mscoree,mshtml=")
            self.run(["wineboot", "--init"], env, "Creating %s" % name)

        def wine(self, name, args, done_text):
            self.run(["wine"] + args, wine_env(prefix_path(name)), done_text)

        def run(self, argv, env, title):
            if self.busy:
                return
            if not shutil.which(argv[0]):
                ui.alert(self, "Wine is not installed", "%s was not found." % argv[0])
                return
            self.busy = True
            self.status.set_text(title + "…")

            def work():
                try:
                    p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=900, check=False)
                    rc, out = p.returncode, (p.stderr or "").strip()
                except (OSError, subprocess.SubprocessError) as e:
                    rc, out = 1, str(e)
                GLib.idle_add(done, rc, out)

            def done(rc, out):
                self.busy = False
                self.status.set_text(title if rc == 0 else "%s failed: %s" % (title, out.splitlines()[-1] if out
                                                                               else rc))
                self.reload()
                return False
            threading.Thread(target=work, daemon=True).start()

        def in_thread(self, fn, fmt):
            def work():
                try:
                    msg = fmt % fn()
                except (OSError, ValueError, tarfile.TarError) as e:
                    msg = "Failed: %s" % e
                GLib.idle_add(lambda: (self.status.set_text(msg), self.reload()) and False)
            self.status.set_text("Working…")
            threading.Thread(target=work, daemon=True).start()

        def delete(self, name):
            path = prefix_path(name)
            if os.path.dirname(path) != prefixes_dir():
                return
            shutil.rmtree(path, ignore_errors=False)
            mapping = {p: n for p, n in load_assignments().items() if n != name}
            save_assignments(mapping)
            self.reload()

        def pick_program(self, name, assign_it):
            dialog = Gtk.FileDialog(title="Choose a Windows program")
            filt = Gtk.FileFilter()
            filt.set_name("Windows programs")
            for pat in ("*.exe", "*.EXE", "*.msi", "*.MSI"):
                filt.add_pattern(pat)
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dialog.set_filters(store)

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is None or not f.get_path():
                    return
                program = f.get_path()
                if assign_it:
                    try:
                        assign(program, name)
                        if name != "default":
                            create_shortcut(os.path.splitext(os.path.basename(program))[0], program, name)
                        self.status.set_text("%s now uses %s and is in the launcher." % (
                            os.path.basename(program), name))
                    except (ValueError, OSError) as e:
                        self.status.set_text("Not assigned: %s" % e)
                    self.reload()
                else:
                    args = ["luxenos-wine-open"] + (["--prefix", name] if name != "default" else []) + [program]
                    ui.spawn(args)
            dialog.open(self, None, done)

        def pick_backup(self):
            dialog = Gtk.FileDialog(title="Choose a Wine backup")

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is None or not f.get_path():
                    return
                path = f.get_path()
                self.ask_name("Restore as", "Restore", lambda name: self.in_thread(
                    lambda: restore(path, name), "Restored to %s"))
            dialog.open(self, None, done)

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv if argv is not None else sys.argv[:1])
