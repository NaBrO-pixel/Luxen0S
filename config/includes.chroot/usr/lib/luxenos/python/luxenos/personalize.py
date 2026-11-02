"""Personalization Studio: the whole look of LuxenOS in one window.

It is a front end for the existing appearance system, not a second one:

  * colours, light/dark, Liquid Glass (intensity, blur, transparency,
    shadows, corners, borders, gaps), fonts, animations, panel position,
    icon and cursor themes: `luxenos-glass set|set-many|preset|intensity|theme`,
    i.e. the per-user ~/.config/luxenos/glass.conf over /etc/luxenos/glass.conf
  * theme packs: luxenos.themes (apply with a keep/revert preview)
  * wallpapers, slideshow, per-monitor and per-workspace: luxenos.wallpaper
  * installing themes from files: luxenos.themeinstall; Debian's theme
    packages through the Software Center (apt verifies them)

Restore: before the first change of a session, glass.conf and wallpaper.conf
are copied to ~/.config/luxenos/personalize-backup/; "Undo this session"
copies them back. "Reset to LuxenOS defaults" runs `luxenos-glass reset`
(which keeps your old overrides as glass.conf.bak) and clears the wallpaper
settings. Reduce motion stays in Settings > Accessibility only, as before.

Usage: luxenos-personalize [--page appearance|glass|fonts|wallpaper|themes]
"""

import os
import shutil
import subprocess
import sys
import threading

from . import config, themeinstall, themes, ui, wallpaper

APP_ID = "org.luxenos.Personalize"
PAGES = ("appearance", "glass", "fonts", "wallpaper", "themes")
PRESETS = ("performance", "balanced", "rich", "flat")
# (key, label, min, max, step, default)
GLASS_SLIDERS = (
    ("GLASS_OPACITY", "Panel opacity (lower is more transparent)", 0, 100, 1, 68),
    ("GLASS_BLUR_RADIUS", "Blur strength (SwayFX)", 0, 10, 1, 5),
    ("GLASS_SHADOW", "Shadows", 0, 100, 1, 35),
    ("GLASS_RADIUS", "Corner radius (panels and windows)", 0, 40, 1, 12),
    ("GLASS_BORDER_WIDTH", "Window border width", 0, 6, 1, 1),
    ("GLASS_BORDER_OPACITY", "Border visibility", 0, 100, 1, 26),
    ("GLASS_GAPS", "Gaps between windows", 0, 32, 1, 6),
    ("GLASS_ANIM_MS", "Animation length (ms; 0 = none)", 0, 600, 10, 120),
)
DEBIAN_THEMES = (
    ("papirus-icon-theme", "Papirus icons"),
    ("breeze-icon-theme", "Breeze icons"),
    ("numix-icon-theme-circle", "Numix Circle icons"),
    ("elementary-icon-theme", "elementary icons"),
    ("breeze-cursor-theme", "Breeze cursors"),
    ("dmz-cursor-theme", "DMZ cursors"),
)


def backup_dir():
    return os.path.join(config.luxenos_config_dir(), "personalize-backup")


def backup_files():
    return (os.path.join(config.luxenos_config_dir(), "glass.conf"), wallpaper.conf_path())


def save_backup():
    b = backup_dir()
    os.makedirs(b, exist_ok=True)
    for src in backup_files():
        dst = os.path.join(b, os.path.basename(src))
        if os.path.exists(src):
            shutil.copy2(src, dst)
            if os.path.exists(dst + ".absent"):
                os.unlink(dst + ".absent")
        else:
            open(dst + ".absent", "w").close()
            if os.path.exists(dst):
                os.unlink(dst)


def restore_backup():
    b = backup_dir()
    if not os.path.isdir(b):
        raise RuntimeError("nothing to undo")
    for dst in backup_files():
        src = os.path.join(b, os.path.basename(dst))
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(src + ".absent") and os.path.exists(dst):
            os.unlink(dst)


def glass(*args):
    exe = shutil.which("luxenos-glass")
    if not exe:
        raise RuntimeError("luxenos-glass is not installed")
    p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=120, check=False)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                           else "luxenos-glass failed")
    return p.stdout


def compositor_fx():
    """True/False/None: does the running compositor accept SwayFX effects?"""
    try:
        out = glass("status")
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "compositor fx" in line:
            return "yes" in line.split("fx", 1)[1]
    return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    start = argv[argv.index("--page") + 1] if "--page" in argv and argv.index("--page") + 1 < len(argv) else None
    Gtk = ui.init()
    from gi.repository import Gdk, Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Personalization")
            self.set_default_size(980, 700)
            self.backed_up = False
            self.pending = {}
            self.pending_id = 0
            self.values = config.glass_values()
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            undo = Gtk.Button(label="Undo this session")
            undo.connect("clicked", lambda *_: self.undo())
            reset = Gtk.Button(label="Reset to defaults…")
            reset.connect("clicked", lambda *_: self.confirm_reset())
            header.pack_end(reset)
            header.pack_end(undo)

            box = Gtk.Box()
            self.stack = Gtk.Stack(hexpand=True)
            self.stack.set_transition_duration(ui.transition_ms(120))
            side = Gtk.StackSidebar(stack=self.stack)
            side.set_size_request(190, -1)
            box.append(side)
            box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            outer.append(self.stack)
            self.status = ui.label("", "lx-small", wrap=True)
            for m in ("start", "end", "bottom"):
                getattr(self.status, "set_margin_" + m)(12)
            outer.append(self.status)
            box.append(outer)
            self.set_child(box)
            self.build_appearance()
            self.build_glass()
            self.build_fonts()
            self.build_wallpaper()
            self.build_themes()
            if start in PAGES:
                self.stack.set_visible_child_name(start)

        # -- helpers --
        def page(self, name, title):
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(18)
            vb.append(ui.label(title, "lx-title"))
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(vb)
            self.stack.add_titled(sw, name, title)
            return vb

        def row(self, parent, text, widget, hint=None):
            r = Gtk.Box(spacing=12)
            lb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            lb.append(ui.label(text))
            if hint:
                lb.append(ui.label(hint, "lx-dim lx-small", wrap=True))
            r.append(lb)
            widget.set_valign(Gtk.Align.CENTER)
            ui.accessible_label(widget, text)
            r.append(widget)
            parent.append(r)
            return widget

        def ensure_backup(self):
            if not self.backed_up:
                save_backup()
                self.backed_up = True

        def run_glass(self, *args, done=None):
            """luxenos-glass in a thread (apply takes a moment); status shown here."""
            self.ensure_backup()
            self.status.set_text("Applying…")

            def work():
                try:
                    glass(*args)
                    err = None
                except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                    err = str(e)
                GLib.idle_add(finish, err)

            def finish(err):
                self.status.set_text("Could not apply: %s" % err if err else "Applied.")
                if err:
                    self.status.add_css_class("lx-crit")
                else:
                    self.status.remove_css_class("lx-crit")
                self.values = config.glass_values()
                if done:
                    done()
                return False

            threading.Thread(target=work, daemon=True).start()

        def set_later(self, key, value):
            """Coalesce slider moves: one luxenos-glass run 400 ms after the last change."""
            if getattr(self, "loading", False):
                return
            self.pending[key] = str(value)
            self.pending_id += 1
            ticket = self.pending_id

            def flush():
                if ticket != self.pending_id or not self.pending:
                    return False
                pairs = ["%s=%s" % kv for kv in self.pending.items()]
                self.pending = {}
                self.run_glass("set-many", *pairs)
                return False

            GLib.timeout_add(400, flush)

        # -- pages --
        def build_appearance(self):
            vb = self.page("appearance", "Appearance")
            dark = Gtk.Switch(active=self.values.get("GLASS_THEME", "dark") != "light")
            self.row(vb, "Dark theme", dark, "Off: light (Daylight) colours everywhere")
            dark.connect("notify::active", lambda w, _p: self.run_glass("theme", "dark" if w.get_active() else "light"))
            color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
            rgba = Gdk.RGBA()
            rgba.parse(self.values.get("GLASS_ACCENT", "#7aa2ff"))
            color.set_rgba(rgba)
            self.row(vb, "Accent colour", color, "Buttons, selections, focus rings, the bar")

            def accent(*_):
                c = color.get_rgba()
                self.run_glass("set", "GLASS_ACCENT", "#%02x%02x%02x" % (int(c.red * 255), int(c.green * 255),
                                                                         int(c.blue * 255)))
            color.connect("notify::rgba", accent)
            bar = Gtk.DropDown.new_from_strings(["Top", "Bottom"])
            bar.set_selected(1 if self.values.get("GLASS_BAR_POSITION") == "bottom" else 0)
            self.row(vb, "Panel position", bar, "Where the top bar (Waybar) sits")
            bar.connect("notify::selected", lambda d, _p: self.run_glass(
                "set", "GLASS_BAR_POSITION", "bottom" if d.get_selected() == 1 else "top"))
            vb.append(ui.label("Theme packs", "lx-heading"))
            vb.append(ui.label("Colours, panels and wallpaper together. Kept only if you confirm within 20 seconds.",
                               "lx-dim lx-small", wrap=True))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8,
                               row_spacing=8)
            for ident, (values, _p) in sorted(themes.available().items()):
                b = Gtk.Button()
                b.add_css_class("lx-tile")
                tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                tb.append(ui.label(values["THEME_NAME"], "lx-heading"))
                tb.append(ui.label(values.get("THEME_DESCRIPTION", ""), "lx-dim lx-small", wrap=True))
                b.set_child(tb)
                b.connect("clicked", lambda _b, i=ident: self.try_pack(i))
                flow.append(b)
            vb.append(flow)

        def try_pack(self, ident):
            self.ensure_backup()
            try:
                themes.apply(ident)
            except (KeyError, RuntimeError, OSError) as e:
                ui.alert(self, "Could not apply the theme pack", str(e))
                return
            answered = {"done": False}

            def choose(i):
                answered["done"] = True
                if i != 1:
                    self.revert_pack()

            dialog = ui.alert(self, "Keep this appearance?", "It reverts automatically in 20 seconds.",
                              buttons=("Revert", "Keep"), default=1, cancel=0, callback=choose)

            def timeout():
                if not answered["done"]:
                    answered["done"] = True
                    self.revert_pack()
                    self.status.set_text("Reverted (no answer within 20 seconds).")
                return False

            GLib.timeout_add_seconds(20, timeout)
            return dialog

        def revert_pack(self):
            try:
                themes.revert()
            except (RuntimeError, OSError) as e:
                self.status.set_text("Revert failed: %s" % e)

        def build_glass(self):
            vb = self.page("glass", "Liquid Glass")
            fx = compositor_fx()
            if fx is False:
                vb.append(ui.label("The running compositor does not support blur, rounded windows or shadows "
                                   "(stock Sway). Panels stay more opaque so text remains readable.",
                                   "lx-banner warning", wrap=True))
            preset = Gtk.DropDown.new_from_strings([p.capitalize() for p in PRESETS])
            cur = self.values.get("GLASS_PRESET", "balanced")
            preset.set_selected(PRESETS.index(cur) if cur in PRESETS else 1)
            self.row(vb, "Preset", preset, "Performance suits older GPUs; Flat turns glass off")
            preset.connect("notify::selected", lambda d, _p: self.run_glass("preset", PRESETS[d.get_selected()],
                                                                           done=self.reload_sliders))
            inten = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            inten.set_size_request(260, -1)
            inten.set_value(config.get_int(self.values, "GLASS_INTENSITY", 55, 0, 100))
            self.row(vb, "Liquid Glass intensity", inten, "One slider for everything below (same as Accessibility)")
            inten.connect("value-changed", lambda s: self.intensity_later(int(s.get_value())))
            blur = Gtk.Switch(active=config.get_bool(self.values, "GLASS_BLUR", True))
            self.row(vb, "Blur behind panels", blur, "Needs SwayFX")
            blur.connect("notify::active", lambda w, _p: self.set_later("GLASS_BLUR", "yes" if w.get_active() else "no"))
            self.sliders = {}
            for key, label, lo, hi, step, default in GLASS_SLIDERS:
                sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
                sc.set_size_request(260, -1)
                sc.set_value(config.get_int(self.values, key, default, lo, hi))
                self.row(vb, label, sc)
                sc.connect("value-changed", lambda s, k=key: self.set_later(k, int(s.get_value())))
                self.sliders[key] = (sc, lo, hi, default)
            vb.append(ui.label("Reduce motion is in Settings › Accessibility.", "lx-dim lx-small"))

        def intensity_later(self, n):
            self.pending_id += 1
            ticket = self.pending_id
            GLib.timeout_add(400, lambda: ticket == self.pending_id and self.run_glass(
                "intensity", str(n), done=self.reload_sliders) and False)

        def reload_sliders(self):
            """Show what a preset/intensity change produced, without writing it back."""
            self.loading = True
            try:
                for key, (sc, lo, hi, default) in self.sliders.items():
                    v = config.get_int(self.values, key, default, lo, hi)
                    if int(sc.get_value()) != v:
                        sc.set_value(v)
            finally:
                self.loading = False

        def build_fonts(self):
            vb = self.page("fonts", "Fonts")
            ctx = self.get_pango_context()
            families = sorted({f.get_name() for f in ctx.list_families()}, key=str.lower)
            cur = self.values.get("GLASS_FONT", "Inter")
            # luxenos-glass accepts letters, digits, spaces, dots, dashes.
            families = [f for f in families if all(c.isalnum() or c in " ._-" for c in f)]
            if cur not in families:
                families.insert(0, cur)
            font = Gtk.DropDown.new_from_strings(families)
            font.set_enable_search(True)
            font.set_selected(families.index(cur))
            self.row(vb, "Interface font", font, "Bar, notifications, GTK and LuxenOS apps (Inter by default)")
            font.connect("notify::selected", lambda d, _p: self.run_glass("set", "GLASS_FONT",
                                                                         families[d.get_selected()]))
            size = Gtk.SpinButton.new_with_range(8, 24, 1)
            size.set_value(config.get_int(self.values, "GLASS_FONT_SIZE", 13, 8, 24))
            self.row(vb, "Font size (px)", size)
            size.connect("value-changed", lambda s: self.set_later("GLASS_FONT_SIZE", int(s.get_value())))
            sample = ui.label("The quick brown fox jumps over the lazy dog. 0123456789", None, wrap=True)
            vb.append(sample)

        # -- wallpaper --
        def build_wallpaper(self):
            vb = self.page("wallpaper", "Wallpaper")
            wv = wallpaper.load()
            target_row = Gtk.Box(spacing=8)
            self.wp_target = Gtk.DropDown.new_from_strings(self.wallpaper_targets())
            ui.accessible_label(self.wp_target, "Apply to")
            target_row.append(ui.label("Apply to"))
            target_row.append(self.wp_target)
            self.wp_mode = Gtk.DropDown.new_from_strings([m.capitalize() for m in wallpaper.MODES])
            self.wp_mode.set_selected(wallpaper.MODES.index(wallpaper.mode(wv)))
            self.wp_mode.connect("notify::selected", lambda d, _p: self.wallpaper_update(
                {"WALLPAPER_MODE": wallpaper.MODES[d.get_selected()]}))
            ui.accessible_label(self.wp_mode, "Scaling")
            target_row.append(ui.label("Scaling"))
            target_row.append(self.wp_mode)
            add = Gtk.Button(label="Add image…")
            add.connect("clicked", lambda *_: self.pick_image())
            target_row.append(add)
            vb.append(target_row)
            self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                    column_spacing=8, row_spacing=8, homogeneous=True)
            vb.append(self.flow)
            self.fill_wallpapers()
            vb.append(ui.label("Slideshow", "lx-heading"))
            show = Gtk.Switch(active=config.get_bool(wv, "SLIDESHOW", False))
            self.row(vb, "Change the wallpaper automatically", show,
                     "Images from the folder below; per-workspace wallpapers still win")
            self.slide_dir = Gtk.Button(label=wv.get("SLIDESHOW_DIR") or "Choose a folder…")
            self.slide_dir.connect("clicked", lambda *_: self.pick_folder())
            self.row(vb, "Folder", self.slide_dir)
            mins = Gtk.SpinButton.new_with_range(5, 1440, 5)
            mins.set_value(config.get_int(wv, "SLIDESHOW_MINUTES", 30, 5, 1440))
            self.row(vb, "Every (minutes)", mins)
            mins.connect("value-changed", lambda s: config.update_kv(
                wallpaper.conf_path(), {"SLIDESHOW_MINUTES": str(int(s.get_value()))},
                header="# LuxenOS wallpaper settings (luxenos-wallpaper)."))
            show.connect("notify::active", lambda w, _p: self.toggle_slideshow(w.get_active()))

        def wallpaper_targets(self):
            self.targets = [("all", None)]
            names = ["All monitors"]
            try:
                from . import swayipc
                with swayipc.Connection() as c:
                    for o in c.get_outputs():
                        if o.get("active"):
                            self.targets.append(("output", o["name"]))
                            names.append("Monitor %s" % o["name"])
                    for ws in c.get_workspaces():
                        if ws.get("num", -1) >= 0:
                            self.targets.append(("workspace", str(ws["num"])))
                            names.append("Workspace %d" % ws["num"])
            except Exception:  # not in Sway: only "all"
                pass
            return names

        def fill_wallpapers(self):
            while (c := self.flow.get_first_child()) is not None:
                self.flow.remove(c)
            imgs = wallpaper.available()[:80]
            if not imgs:
                self.flow.append(ui.label("No wallpapers found. Put images in ~/Pictures/Wallpapers.", "lx-dim"))
            for p in imgs:
                b = Gtk.Button()
                b.add_css_class("lx-tile")
                pic = Gtk.Picture.new_for_filename(p)
                pic.set_can_shrink(True)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_size_request(180, 110)
                b.set_child(pic)
                ui.accessible_label(b, os.path.basename(p))
                b.connect("clicked", lambda _b, path=p: self.choose_wallpaper(path))
                self.flow.append(b)

        def wallpaper_update(self, changes, remove=()):
            self.ensure_backup()
            try:
                wallpaper.update(changes, remove)
                self.status.set_text("Wallpaper updated.")
            except (ValueError, OSError) as e:
                self.status.set_text("Wallpaper not changed: %s" % e)

        def choose_wallpaper(self, path):
            kind, name = self.targets[self.wp_target.get_selected()]
            if kind == "all":
                self.wallpaper_update({"WALLPAPER": path})
            elif kind == "output":
                key = wallpaper.output_key(name)
                self.wallpaper_update({key: path, key + "_NAME": name})
            else:
                self.wallpaper_update({"WALLPAPER_WS_%s" % name: path})
            if wallpaper.dynamic(wallpaper.load()):
                ui.spawn(["luxenos-wallpaper", "daemon"])

        def file_dialog(self, title, folder, callback):
            dialog = Gtk.FileDialog(title=title)
            if not folder:
                filt = Gtk.FileFilter()
                filt.set_name("Images")
                for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.PNG", "*.JPG", "*.JPEG"):
                    filt.add_pattern(pat)
                store = Gio.ListStore.new(Gtk.FileFilter)
                store.append(filt)
                dialog.set_filters(store)

            def done(d, res):
                try:
                    f = (d.select_folder_finish if folder else d.open_finish)(res)
                except GLib.Error:
                    return
                if f is not None and f.get_path():
                    callback(f.get_path())

            (dialog.select_folder if folder else dialog.open)(self, None, done)

        def pick_image(self):
            def got(path):
                try:
                    dest = themeinstall.install_wallpaper(path, os.path.basename(path))
                except OSError as e:
                    self.status.set_text("Could not add the image: %s" % e)
                    return
                self.fill_wallpapers()
                self.choose_wallpaper(dest)
            self.file_dialog("Add a wallpaper", False, got)

        def pick_folder(self):
            def got(path):
                if not wallpaper.images_in(path):
                    self.status.set_text("No images in %s" % path)
                    return
                self.slide_dir.set_label(path)
                config.update_kv(wallpaper.conf_path(), {"SLIDESHOW_DIR": path},
                                 header="# LuxenOS wallpaper settings (luxenos-wallpaper).")
            self.file_dialog("Slideshow folder", True, got)

        def toggle_slideshow(self, on):
            wv = wallpaper.load()
            if on and not wv.get("SLIDESHOW_DIR"):
                self.status.set_text("Choose a folder first.")
                return
            config.update_kv(wallpaper.conf_path(), {"SLIDESHOW": "yes" if on else "no"},
                             header="# LuxenOS wallpaper settings (luxenos-wallpaper).")
            if on:
                ui.spawn(["luxenos-wallpaper", "daemon"])
            self.status.set_text("Slideshow %s." % ("on" if on else "off"))

        # -- themes --
        def build_themes(self):
            vb = self.page("themes", "Themes and icons")
            icons = themeinstall.installed("icons")
            cursors = themeinstall.installed("cursors")
            for key, label, names in (("GLASS_ICON_THEME", "Icon theme", icons),
                                      ("GLASS_CURSOR_THEME", "Cursor theme", cursors)):
                cur = self.values.get(key, "Adwaita")
                names = names or ["Adwaita"]
                if cur not in names:
                    names = [cur] + names
                dd = Gtk.DropDown.new_from_strings(names)
                dd.set_selected(names.index(cur))
                self.row(vb, label, dd)
                dd.connect("notify::selected", lambda d, _p, k=key, n=names: self.run_glass("set", k,
                                                                                           n[d.get_selected()]))
            size = Gtk.SpinButton.new_with_range(16, 96, 8)
            size.set_value(config.get_int(self.values, "GLASS_CURSOR_SIZE", 24, 16, 96))
            self.row(vb, "Cursor size", size)
            size.connect("value-changed", lambda s: self.set_later("GLASS_CURSOR_SIZE", int(s.get_value())))
            vb.append(ui.label("Application style stays Adwaita with the Liquid Glass colours; other GTK themes "
                               "you install are available to tools like nwg-look.", "lx-dim lx-small", wrap=True))
            vb.append(ui.label("Install from a file", "lx-heading"))
            vb.append(ui.label("GTK, icon and cursor themes (.tar.gz, .tar.xz, .zip), wallpapers and Liquid Glass "
                               "presets (.theme). Checked before anything is written; installed for you only.",
                               "lx-dim lx-small", wrap=True))
            inst = Gtk.Button(label="Install theme file…", halign=Gtk.Align.START)
            inst.connect("clicked", lambda *_: self.install_file())
            vb.append(inst)
            vb.append(ui.label("From Debian", "lx-heading"))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=8,
                               row_spacing=8)
            for pkgname, title in DEBIAN_THEMES:
                b = Gtk.Button(label=title)
                ui.accessible_label(b, "Install %s from Debian (%s)" % (title, pkgname))
                b.connect("clicked", lambda _b, n=pkgname: ui.spawn(["luxenos-software", "--install", n]))
                flow.append(b)
            vb.append(flow)

        def install_file(self):
            dialog = Gtk.FileDialog(title="Install a theme")

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is None or not f.get_path():
                    return
                self.do_install(f.get_path(), False)

            dialog.open(self, None, done)

        def do_install(self, path, replace):
            try:
                done = themeinstall.install(path, replace)
            except themeinstall.InstallError as e:
                if "already" in str(e) and not replace:
                    ui.alert(self, "Replace the installed theme?", str(e), buttons=("Cancel", "Replace"),
                             default=0, cancel=0, callback=lambda i: i == 1 and self.do_install(path, True))
                else:
                    ui.alert(self, "This file was not installed", str(e))
                return
            except OSError as e:
                ui.alert(self, "This file was not installed", str(e))
                return
            self.status.set_text("Installed: " + ", ".join("%s (%s)" % (os.path.basename(d), k) for k, d in done))

        # -- restore --
        def undo(self):
            try:
                restore_backup()
            except (RuntimeError, OSError) as e:
                self.status.set_text("Nothing undone: %s" % e)
                return
            wallpaper.apply()
            self.run_glass("apply")

        def confirm_reset(self):
            ui.alert(self, "Reset the appearance?", "Colours, Liquid Glass, fonts, icons and wallpaper go back to the "
                     "LuxenOS defaults. Your previous glass settings are kept as glass.conf.bak.",
                     buttons=("Cancel", "Reset"), default=0, cancel=0,
                     callback=lambda i: i == 1 and self.reset())

        def reset(self):
            self.ensure_backup()
            try:
                os.unlink(wallpaper.conf_path())
            except FileNotFoundError:
                pass
            wallpaper.apply()
            self.run_glass("reset")

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()
            self.theme_monitor = ui.watch_theme()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv[:1])
