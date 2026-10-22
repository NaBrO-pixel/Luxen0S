"""LuxenOS System Monitor.

CPU, memory, network and battery with history graphs, running processes
with their CPU and memory use, and disk space, all read from /proc and /sys
(luxenos.sysinfo). Sampling runs only while the window is visible, processes
are only scanned while their tab is open, and the interval is adjustable
(or paused), so the monitor itself stays cheap on old hardware.

Ending a process always asks first. Your own processes are signalled
directly; other users' and system processes need an administrator, through
pkexec (polkit asks for the password each time). PID 1 and kernel threads
are never offered.
"""

import os
import signal
import subprocess
import sys
from collections import deque

from . import config, sysinfo, ui

APP_ID = "org.luxenos.SystemMonitor"
HISTORY = 60
INTERVALS = (("1 second", 1000), ("2 seconds", 2000), ("5 seconds", 5000), ("Paused", 0))
SESSION_CRITICAL = {"sway", "Xwayland", "greetd", "systemd", "dbus-daemon", "dbus-broker",
                    "pipewire", "wireplumber", "NetworkManager", "waybar", "polkitd"}


def warnings_for(state):
    """Human-readable warnings from one sample (pure, unit tested)."""
    out = []
    if state.get("cpu_high_samples", 0) >= 5:
        out.append(("warning", "CPU has been above 90% for a while. Check the Processes tab for the cause."))
    mem = state.get("memory")
    if mem and mem["total"]:
        used = 100.0 * mem["used"] / mem["total"]
        if used >= 95:
            out.append(("error", "Memory is almost full (%d%%). Apps may be closed by the system." % used))
        elif used >= 90:
            out.append(("warning", "Memory use is high (%d%%)." % used))
        if mem["swap_total"] and 100.0 * mem["swap_used"] / mem["swap_total"] >= 80:
            out.append(("warning", "Swap is %d%% full; the system may become slow." %
                        (100 * mem["swap_used"] // mem["swap_total"])))
    for d in state.get("disks", []):
        level = sysinfo.disk_warning(d)
        if level:
            out.append(("error" if level == "critical" else "warning",
                        "Low disk space on %s: %s free." % (d["mount"], ui.human_bytes(d["free"]))))
    bats = state.get("batteries") or []
    pct, _ = sysinfo.battery_summary(bats) if bats else (None, "")
    if pct is not None and pct <= 10 and bats[0]["status"] == "Discharging":
        out.append(("error", "Battery is low (%d%%). Connect the charger." % pct))
    return out


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gio, GLib, Pango

    accent_hex = config.glass_values().get("GLASS_ACCENT", "#7aa2ff")
    try:
        ACCENT = tuple(int(accent_hex[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    except ValueError:
        ACCENT = (0.48, 0.64, 1.0)
    SECOND = (0.94, 0.75, 0.35)

    class Graph(Gtk.DrawingArea):
        """Line graph of up to two series; values 0..max (auto-scaled if max is None)."""

        def __init__(self, maximum=100.0, series=1):
            super().__init__()
            self.add_css_class("lx-graph")
            self.set_content_height(90)
            self.set_hexpand(True)
            self.maximum = maximum
            self.data = [deque([0.0] * HISTORY, maxlen=HISTORY) for _ in range(series)]
            self.set_draw_func(self.draw)

        def push(self, *values):
            for d, v in zip(self.data, values):
                d.append(float(v))
            self.queue_draw()

        def draw(self, _area, cr, w, h):
            top = self.maximum or max(1.0, max(max(d) for d in self.data) * 1.15)
            cr.set_line_width(1)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            for frac in (0.25, 0.5, 0.75):
                y = round(h * frac) + 0.5
                cr.move_to(0, y)
                cr.line_to(w, y)
            cr.stroke()
            for d, color in zip(self.data, (ACCENT, SECOND)):
                step = w / (HISTORY - 1)
                cr.move_to(0, h)
                for i, v in enumerate(d):
                    cr.line_to(i * step, h - min(1.0, v / top) * (h - 2))
                cr.line_to(w, h)
                cr.close_path()
                cr.set_source_rgba(color[0], color[1], color[2], 0.18)
                cr.fill_preserve()
                cr.new_path()
                for i, v in enumerate(d):
                    y = h - min(1.0, v / top) * (h - 2)
                    if i == 0:
                        cr.move_to(0, y)
                    else:
                        cr.line_to(i * step, y)
                cr.set_source_rgba(color[0], color[1], color[2], 0.95)
                cr.set_line_width(2)
                cr.stroke()

    def card(title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("lx-card")
        head = Gtk.Box(spacing=8)
        head.append(ui.label(title, "lx-heading"))
        value = ui.label("", "lx-dim")
        value.set_hexpand(True)
        value.set_xalign(1.0)
        head.append(value)
        box.append(head)
        return box, value

    class MonitorWindow(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="System Monitor")
            self.set_default_size(900, 640)
            self.sampler = sysinfo.Sampler()
            self.interval = 2000
            self.timer = None
            self.cpu_high = 0
            self.disks = []
            self.disk_age = 99
            self.processes = {}

            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(ui.transition_ms(150))
            switcher = Gtk.StackSwitcher(stack=self.stack)
            header.set_title_widget(switcher)
            names = [n for n, _ in INTERVALS]
            self.interval_dd = Gtk.DropDown.new_from_strings(names)
            self.interval_dd.set_selected(1)
            self.interval_dd.set_tooltip_text("Refresh interval")
            ui.accessible_label(self.interval_dd, "Refresh interval")
            self.interval_dd.connect("notify::selected", self.on_interval)
            header.pack_end(self.interval_dd)
            header.pack_end(ui.icon_button("view-refresh-symbolic", "Refresh now", self.tick_once, css="flat"))

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.banners.set_margin_start(12)
            self.banners.set_margin_end(12)
            self.banners.set_margin_top(8)
            outer.append(self.banners)
            outer.append(self.stack)
            self.set_child(outer)

            self.build_overview()
            self.build_processes()
            self.build_storage()
            self.stack.connect("notify::visible-child", lambda *_: self.tick_once())
            self.connect("notify::visible", lambda *_: self.reschedule())
            self.connect("close-request", self.on_close)
            self.tick_once()
            self.reschedule()

        # -- pages --
        def build_overview(self):
            grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
            for m in ("start", "end", "top", "bottom"):
                getattr(grid, "set_margin_" + m)(12)
            c, self.cpu_value = card("Processor")
            self.cpu_graph = Graph(100)
            c.append(self.cpu_graph)
            c.append(ui.label("%d logical CPU%s" % (sysinfo.cpu_count(), "s" if sysinfo.cpu_count() > 1 else ""),
                              "lx-dim lx-small"))
            grid.attach(c, 0, 0, 1, 1)
            c, self.mem_value = card("Memory")
            self.mem_graph = Graph(100)
            c.append(self.mem_graph)
            self.swap_label = ui.label("", "lx-dim lx-small")
            c.append(self.swap_label)
            grid.attach(c, 1, 0, 1, 1)
            c, self.net_value = card("Network")
            self.net_graph = Graph(None, series=2)
            c.append(self.net_graph)
            c.append(ui.label("Blue: download · Orange: upload", "lx-dim lx-small"))
            grid.attach(c, 0, 1, 1, 1)
            c, self.bat_value = card("Battery and power")
            self.bat_detail = ui.label("", "lx-dim", wrap=True)
            c.append(self.bat_detail)
            grid.attach(c, 1, 1, 1, 1)
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(grid)
            self.stack.add_titled(scroller, "overview", "Overview")

        def build_processes(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(12)
            bar = Gtk.Box(spacing=8)
            self.proc_search = Gtk.SearchEntry(placeholder_text="Filter by name, command or user")
            self.proc_search.set_hexpand(True)
            self.proc_search.connect("search-changed", lambda *_: self.fill_processes())
            bar.append(self.proc_search)
            self.sort_dd = Gtk.DropDown.new_from_strings(["Sort by CPU", "Sort by memory", "Sort by name", "Sort by PID"])
            ui.accessible_label(self.sort_dd, "Sort processes")
            self.sort_dd.connect("notify::selected", lambda *_: self.fill_processes())
            bar.append(self.sort_dd)
            self.mine_only = Gtk.CheckButton(label="Only my processes")
            self.mine_only.connect("toggled", lambda *_: self.fill_processes())
            bar.append(self.mine_only)
            self.end_btn = Gtk.Button(label="End process…")
            self.end_btn.add_css_class("destructive-action")
            self.end_btn.set_sensitive(False)
            self.end_btn.connect("clicked", lambda *_: self.confirm_end())
            bar.append(self.end_btn)
            box.append(bar)
            head = self.proc_row_box(("Name", "User", "CPU", "Memory", "PID"), header=True)
            box.append(head)
            self.proc_list = Gtk.ListBox()
            self.proc_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.proc_list.connect("row-selected", self.on_proc_selected)
            self.proc_list.connect("row-activated", lambda *_: self.confirm_end())
            scroller = Gtk.ScrolledWindow(vexpand=True)
            scroller.set_child(self.proc_list)
            box.append(scroller)
            self.proc_count = ui.label("", "lx-dim lx-small")
            box.append(self.proc_count)
            self.stack.add_titled(box, "processes", "Processes")

        def proc_row_box(self, cols, header=False):
            hb = Gtk.Box(spacing=8)
            widths = (None, 10, 7, 10, 8)
            for i, (text, w) in enumerate(zip(cols, widths)):
                lbl = ui.label(text, "lx-heading" if header else None, xalign=0.0 if i < 2 else 1.0)
                if w is None:
                    lbl.set_hexpand(True)
                    lbl.set_ellipsize(Pango.EllipsizeMode.END)
                else:
                    lbl.set_width_chars(w)
                    lbl.set_ellipsize(Pango.EllipsizeMode.END)
                hb.append(lbl)
            if header:
                hb.set_margin_start(6)
                hb.set_margin_end(18)
            return hb

        def build_storage(self):
            self.storage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(self.storage_box, "set_margin_" + m)(12)
            scroller = Gtk.ScrolledWindow()
            scroller.set_child(self.storage_box)
            self.stack.add_titled(scroller, "storage", "Storage")

        # -- sampling --
        def on_interval(self, *_):
            self.interval = INTERVALS[self.interval_dd.get_selected()][1]
            self.reschedule()

        def reschedule(self):
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            if self.interval and self.get_visible():
                self.timer = GLib.timeout_add(self.interval, self.tick)

        def on_close(self, *_):
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            return False

        def tick(self):
            if self.is_suspended():
                return True  # minimized/hidden: skip the work, keep the timer
            self.tick_once()
            return True

        def is_suspended(self):
            surface = self.get_surface()
            try:
                from gi.repository import Gdk
                return bool(surface and surface.get_state() & Gdk.ToplevelState.SUSPENDED)
            except (AttributeError, TypeError):
                return False

        def tick_once(self):
            page = self.stack.get_visible_child_name()
            s = self.sampler.sample(with_processes=(page == "processes"))
            cpu = s["cpu"]
            self.cpu_high = self.cpu_high + 1 if cpu >= 90 else 0
            self.cpu_graph.push(cpu)
            self.cpu_value.set_text("%.0f%%" % cpu)
            mem = s["memory"]
            if mem and mem["total"]:
                pct = 100.0 * mem["used"] / mem["total"]
                self.mem_graph.push(pct)
                self.mem_value.set_text("%s of %s (%.0f%%)" % (ui.human_bytes(mem["used"]),
                                                               ui.human_bytes(mem["total"]), pct))
                self.swap_label.set_text("Swap: %s of %s" % (ui.human_bytes(mem["swap_used"]),
                                                              ui.human_bytes(mem["swap_total"]))
                                         if mem["swap_total"] else "No swap configured")
            rx, tx = s["net"]
            self.net_graph.push(rx, tx)
            self.net_value.set_text("↓ %s/s  ↑ %s/s" % (ui.human_bytes(rx), ui.human_bytes(tx)))
            bats = sysinfo.read_batteries()
            self.update_battery(bats)
            self.disk_age += 1
            if self.disk_age * max(self.interval, 1000) >= 30000 or page == "storage":
                self.disks = sysinfo.read_disks()
                self.disk_age = 0
                self.update_storage()
            if "processes" in s:
                self.processes = s["processes"]
                self.fill_processes()
            self.update_banners(warnings_for({"cpu_high_samples": self.cpu_high, "memory": mem,
                                              "disks": self.disks, "batteries": bats}))
            return True

        def update_battery(self, bats):
            ac = sysinfo.ac_online()
            if not bats:
                self.bat_value.set_text("No battery")
                self.bat_detail.set_text("Running on mains power." if ac is not False else
                                         "No battery information is available on this hardware.")
                return
            pct, summary = sysinfo.battery_summary(bats)
            self.bat_value.set_text(summary)
            lines = []
            for b in bats:
                parts = ["%s: %s%%" % (b["name"], b["capacity"] if b["capacity"] is not None else "?"), b["status"]]
                if b["power_w"]:
                    parts.append("%.1f W" % b["power_w"])
                if b["health"]:
                    parts.append("health %d%% of design capacity" % b["health"])
                lines.append(", ".join(parts))
            if ac is not None:
                lines.append("Charger: %s" % ("connected" if ac else "not connected"))
            self.bat_detail.set_text("\n".join(lines))

        def update_storage(self):
            child = self.storage_box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.storage_box.remove(child)
                child = nxt
            if not self.disks:
                self.storage_box.append(ui.label("No disks could be read.", "lx-dim"))
            for d in self.disks:
                c, value = card(d["mount"])
                value.set_text("%s free of %s" % (ui.human_bytes(d["free"]), ui.human_bytes(d["total"])))
                bar = Gtk.LevelBar(min_value=0, max_value=1)
                bar.set_value(min(1.0, d["used"] / d["total"]))
                bar.add_offset_value("low", 0.80)
                bar.add_offset_value("high", 0.90)
                bar.add_offset_value("full", 1.0)
                c.append(bar)
                level = sysinfo.disk_warning(d)
                note = "%s · %s" % (d["device"], d["fstype"])
                if level:
                    note += " · low on space: delete or move files, or empty the trash"
                c.append(ui.label(note, "lx-small " + ("lx-crit" if level == "critical" else
                                                        "lx-warn" if level else "lx-dim")))
                ui.accessible_label(bar, "%s: %s free of %s" % (d["mount"], ui.human_bytes(d["free"]),
                                                                ui.human_bytes(d["total"])))
                self.storage_box.append(c)

        def update_banners(self, warns):
            child = self.banners.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.banners.remove(child)
                child = nxt
            for level, text in warns:
                b = ui.label(text, "lx-banner " + level, wrap=True)
                self.banners.append(b)

        # -- processes --
        def fill_processes(self):
            selected = self.proc_list.get_selected_row()
            sel_pid = selected.pid if selected is not None else None
            q = self.proc_search.get_text().lower()
            me = os.getuid()
            procs = [p for p in self.processes.values() if not p["kernel"]]
            if self.mine_only.get_active():
                procs = [p for p in procs if p["uid"] == me]
            if q:
                procs = [p for p in procs if q in p["name"].lower() or q in p["cmdline"].lower()
                         or q in p["user"].lower()]
            key = self.sort_dd.get_selected()
            if key == 0:
                procs.sort(key=lambda p: (-p.get("cpu", 0), -p["rss"]))
            elif key == 1:
                procs.sort(key=lambda p: -p["rss"])
            elif key == 2:
                procs.sort(key=lambda p: p["name"].lower())
            else:
                procs.sort(key=lambda p: p["pid"])
            self.proc_list.remove_all()
            shown = procs[:250]
            for p in shown:
                row = Gtk.ListBoxRow()
                row.pid = p["pid"]
                row.set_child(self.proc_row_box((p["name"], p["user"], "%.1f%%" % p.get("cpu", 0),
                                                 ui.human_bytes(p["rss"]), str(p["pid"]))))
                row.set_tooltip_text(p["cmdline"] or p["name"])
                ui.accessible_label(row, "%s, user %s, CPU %.0f percent, memory %s, process %d" % (
                    p["name"], p["user"], p.get("cpu", 0), ui.human_bytes(p["rss"]), p["pid"]))
                self.proc_list.append(row)
                if p["pid"] == sel_pid:
                    self.proc_list.select_row(row)
            self.proc_count.set_text("%d processes%s" % (len(procs), " (showing the first 250)" if len(procs) > 250 else ""))

        def on_proc_selected(self, _l, row):
            self.end_btn.set_sensitive(row is not None)

        def confirm_end(self):
            row = self.proc_list.get_selected_row()
            if row is None:
                return
            p = self.processes.get(row.pid)
            if p is None:
                return
            if p["pid"] in (1, os.getpid()) or p["kernel"]:
                ui.alert(self, "This process cannot be ended here", "%s (%d) is essential to the system."
                         % (p["name"], p["pid"]))
                return
            own = p["uid"] == os.getuid()
            body = "%s (process %d, user %s)\n%s" % (p["name"], p["pid"], p["user"], p["cmdline"][:200])
            if p["name"] in SESSION_CRITICAL:
                body += "\n\nWarning: ending this can close your desktop session or stop sound/network."
            body += "\n\nUnsaved work in this program may be lost."
            if not own:
                body += "\nIt belongs to another user, so an administrator password is required."
            buttons = ("Cancel", "End process", "Force stop")

            def chosen(idx):
                if idx in (1, 2):
                    self.signal_process(p, signal.SIGTERM if idx == 1 else signal.SIGKILL, own)

            ui.alert(self, "End “%s”?" % p["name"], body, buttons=buttons, default=0, cancel=0, callback=chosen)

        def signal_process(self, p, sig, own):
            current = sysinfo.read_processes().get(p["pid"])
            if current is None or current["start"] != p["start"]:
                ui.alert(self, "The process already ended", "Nothing was done.")
                return
            if own:
                try:
                    os.kill(p["pid"], sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    ui.alert(self, "Permission denied", "You are not allowed to end this process.")
                return
            # pkexec runs /usr/bin/kill as root after polkit authentication
            # (auth_admin); arguments are a fixed argv, never a shell string.
            name = "KILL" if sig == signal.SIGKILL else "TERM"
            try:
                r = subprocess.run(["pkexec", "/usr/bin/kill", "-s", name, "--", str(p["pid"])],
                                   capture_output=True, text=True, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError) as e:
                ui.alert(self, "Could not end the process", str(e))
                return
            if r.returncode in (126, 127):
                ui.alert(self, "Not authorized", "Administrator authentication was cancelled or failed.")
            elif r.returncode != 0:
                ui.alert(self, "Could not end the process", (r.stderr or r.stdout).strip())

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            win = self.get_active_window() or MonitorWindow(self)
            win.present()

    return App().run(argv if argv is not None else sys.argv)
