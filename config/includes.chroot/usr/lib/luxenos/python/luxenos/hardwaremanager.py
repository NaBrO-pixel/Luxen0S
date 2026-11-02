"""LuxenOS Hardware Manager.

What is inside this computer, which driver each device uses, firmware
versions, and a compatibility overview (Android support, graphics and
network drivers, boot mode). Data comes from luxenos.hardware (kernel
interfaces, lspci, lsusb, lsblk, fwupd); the Secure Boot page runs
`luxenos-secureboot`, which only reads and never changes firmware keys.

"Copy report" puts a plain-text summary on the clipboard for bug reports.
Firmware updates are installed from the Update Manager.

Command line: luxenos-hardware [--report]   (--report: print the report, no window)
"""

import subprocess
import sys
import threading

from . import hardware, ui

APP_ID = "org.luxenos.HardwareManager"
ICONS = {"ok": "emblem-ok-symbolic", "warning": "dialog-warning-symbolic", "problem": "dialog-error-symbolic"}
CSS = {"ok": None, "warning": "lx-warn", "problem": "lx-crit"}
SECUREBOOT = "luxenos-secureboot"


def report_text(data):
    lines = ["LuxenOS hardware report"]
    for level, title, advice in data["checks"]:
        lines.append("[%s] %s%s" % (level, title, (": " + advice) if advice else ""))
    for section, rows in data["sections"].items():
        lines.append("")
        lines.append("== %s" % section)
        for k, v in rows:
            lines.append("%s: %s" % (k, v) if v else k)
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)
    if "--report" in argv[1:]:
        sys.stdout.write(report_text(hardware.collect()))
        return 0
    Gtk = ui.init()
    from gi.repository import Gdk, Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Hardware")
            self.set_default_size(940, 640)
            self.data = None
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            header.pack_start(ui.icon_button("view-refresh-symbolic", "Scan again", self.scan))
            self.copy_btn = Gtk.Button(label="Copy report")
            self.copy_btn.connect("clicked", lambda *_: self.copy_report())
            header.pack_end(self.copy_btn)
            fw = Gtk.Button(label="Firmware updates…")
            fw.connect("clicked", lambda *_: ui.spawn(["luxenos-update-manager"]))
            header.pack_end(fw)

            box = Gtk.Box()
            self.stack = Gtk.Stack(hexpand=True)
            self.stack.set_transition_duration(ui.transition_ms(120))
            side = Gtk.StackSidebar(stack=self.stack)
            side.set_size_request(200, -1)
            box.append(side)
            box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            box.append(self.stack)
            self.set_child(box)
            self.placeholder = ui.label("Scanning hardware…", "lx-title")
            self.stack.add_titled(self.placeholder, "loading", "Overview")
            self.scan()

        def scan(self):
            self.copy_btn.set_sensitive(False)
            threading.Thread(target=lambda: GLib.idle_add(self.show, hardware.collect()), daemon=True).start()

        def page(self):
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(16)
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_child(vb)
            return sw, vb

        def show(self, data):
            self.data = data
            while (c := self.stack.get_first_child()) is not None:
                self.stack.remove(c)
            sw, vb = self.page()
            vb.append(ui.label("Compatibility", "lx-title"))
            for level, title, advice in data["checks"]:
                row = Gtk.Box(spacing=10)
                row.add_css_class("lx-card")
                row.append(Gtk.Image.new_from_icon_name(ICONS[level]))
                tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                tb.append(ui.label(title, " ".join(filter(None, ["lx-heading", CSS[level]])), wrap=True))
                if advice:
                    tb.append(ui.label(advice, "lx-dim lx-small", wrap=True))
                row.append(tb)
                ui.accessible_label(row, "%s: %s. %s" % (level, title, advice))
                vb.append(row)
            self.stack.add_titled(sw, "overview", "Overview")
            for section, rows in data["sections"].items():
                sw, vb = self.page()
                vb.append(ui.label(section, "lx-title"))
                if not rows:
                    vb.append(ui.label("Nothing found (or the tool to read it is not installed).", "lx-dim"))
                grid = Gtk.Grid(column_spacing=18, row_spacing=6)
                for i, (k, v) in enumerate(rows):
                    kl = ui.label(k, "lx-heading", wrap=True, selectable=True)
                    kl.set_max_width_chars(40)
                    grid.attach(kl, 0, i, 1, 1)
                    grid.attach(ui.label(v, None, wrap=True, selectable=True), 1, i, 1, 1)
                vb.append(grid)
                self.stack.add_titled(sw, section.lower().replace(" ", "-"), section)
            self.stack.add_titled(self.secure_boot_page(), "secureboot", "Secure Boot")
            self.copy_btn.set_sensitive(True)
            return False

        def secure_boot_page(self):
            sw, vb = self.page()
            vb.append(ui.label("Secure Boot", "lx-title"))
            out = ui.label("", None, wrap=True, selectable=True)
            vb.append(out)
            try:
                p = subprocess.run([SECUREBOOT, "check", "--plain"], capture_output=True, text=True,
                                   timeout=20, check=False)
                out.set_text(p.stdout.strip() or p.stderr.strip())
            except (OSError, subprocess.SubprocessError) as e:
                out.set_text("luxenos-secureboot could not be run: %s" % e)
            full = Gtk.Button(label="Check the EFI system partition too…", halign=Gtk.Align.START)
            ui.accessible_label(full, "Full boot-chain check (administrator password)")

            def run_full(*_):
                full.set_sensitive(False)
                lines = []

                def done(rc):
                    full.set_sensitive(True)
                    if rc in (126, 127) and not lines:
                        return
                    out.set_text("\n".join(l for l in lines if not l.startswith("@@")))

                ui.run_streaming(["pkexec", "/usr/local/bin/luxenos-secureboot", "check"], lines.append, done)

            full.connect("clicked", run_full)
            vb.append(full)
            vb.append(ui.label(
                "LuxenOS never turns Secure Boot on or off and never changes firmware keys. To use Secure Boot, "
                "build the image with LUXENOS_SECURE_BOOT=1 (signed shim and GRUB), then enable it in the "
                "firmware settings. Custom kernels or modules (for example DKMS drivers) need a Machine Owner Key: "
                "`sudo mokutil --import key.der`, then confirm it in the blue MokManager screen at the next boot. "
                "If the computer no longer boots, disable Secure Boot in the firmware settings; nothing on the "
                "disk has to change. Details: README.md, “Secure Boot”.", "lx-dim lx-small", wrap=True))
            return sw

        def copy_report(self):
            if self.data is None:
                return
            Gdk.Display.get_default().get_clipboard().set(report_text(self.data))
            self.copy_btn.set_label("Copied")
            GLib.timeout_add_seconds(2, lambda: self.copy_btn.set_label("Copy report") or False)

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv[:1])
