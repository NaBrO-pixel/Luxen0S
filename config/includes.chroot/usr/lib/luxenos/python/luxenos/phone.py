"""Phone Connect: your phone and LuxenOS together.

Built on KDE Connect (Debian package "kdeconnect", installed on request from
this window), whose free Android/iOS app is the other end. LuxenOS does not
invent its own protocol: KDE Connect already gives what matters for safety,

  * encryption: every connection is TLS, the device certificate is pinned
    when you pair;
  * explicit pairing: nothing is shared until you accept a pairing request on
    both devices, and you can unpair at any time;
  * no account and no cloud: devices talk directly on your local network.

The LuxenOS firewall blocks KDE Connect's ports (1714-1764) by default.
"Allow on this network" opens them for your current local network only
(private address ranges), through luxenos-firewall-root; "Block" closes them.

Features: discovery, pairing, sending files, sending the clipboard (Wayland
does not let background programs read the clipboard, so automatic sync
from the computer is unreliable; "Send clipboard" always works), ringing a
lost phone, the phone's notifications (KDE Connect shows them as desktop
notifications; listed here too), and media controls (the phone's players
appear as MPRIS players: the Music widget and media keys control them).
"""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading

from . import ui

APP_ID = "org.luxenos.Phone"
CLI = "kdeconnect-cli"
FIREWALL = "/usr/libexec/luxenos/luxenos-firewall-root"
ID_RE = re.compile(r"^[A-Za-z0-9_{}-]{8,64}$")
_DEV_RE = re.compile(r"^- (.*): (\S+) \((.*)\)$")


def installed():
    return shutil.which(CLI) is not None


def parse_devices(text):
    """`kdeconnect-cli -l` -> [{'name','id','paired','reachable'}]"""
    out = []
    for line in text.splitlines():
        m = _DEV_RE.match(line.strip())
        if not m or not ID_RE.match(m.group(2)):
            continue
        state = m.group(3).lower()
        out.append({"name": m.group(1), "id": m.group(2), "paired": "paired" in state and "not paired" not in state,
                    "reachable": "reachable" in state and "unreachable" not in state})
    return out


def cli(*args, timeout=15):
    if not installed():
        return 127, "KDE Connect is not installed"
    try:
        p = subprocess.run([CLI] + list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "KDE Connect did not answer"
    except OSError as e:
        return 126, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def devices(refresh=False):
    if refresh:
        cli("--refresh", timeout=10)
    rc, out = cli("-l")
    return parse_devices(out) if rc == 0 else []


def local_network(ip_json):
    """`ip -j -4 addr` + default route -> 'a.b.c.0/nn' of a private network, or None."""
    try:
        data = json.loads(ip_json)
    except ValueError:
        return None
    for iface in data:
        if iface.get("ifname") in ("lo", "waydroid0") or iface.get("operstate") == "DOWN":
            continue
        for a in iface.get("addr_info", []):
            if a.get("family") != "inet":
                continue
            try:
                net = ipaddress.ip_network("%s/%s" % (a["local"], a["prefixlen"]), strict=False)
            except (KeyError, ValueError):
                continue
            if net.is_private and 16 <= net.prefixlen <= 30:
                return str(net)
    return None


def current_network():
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return local_network(out)


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gdk, Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Phone")
            self.set_default_size(760, 600)
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            header.pack_start(ui.icon_button("view-refresh-symbolic", "Look for devices",
                                             lambda: self.refresh(True)))
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            for m in ("start", "end", "top", "bottom"):
                getattr(vb, "set_margin_" + m)(16)
            self.set_child(vb)
            vb.append(ui.label("Phone Connect", "lx-title"))
            vb.append(ui.label("Install the free KDE Connect app on your phone, connect both to the same Wi-Fi, "
                               "then pair. Connections are encrypted and only paired devices can exchange anything.",
                               "lx-dim", wrap=True))
            if not installed():
                vb.append(ui.label("Phone support is not installed yet (Debian package kdeconnect, about 60 MB "
                                   "with its libraries).", "lx-banner info", wrap=True))
                b = Gtk.Button(label="Install phone support…", halign=Gtk.Align.START)
                b.add_css_class("suggested-action")
                b.connect("clicked", lambda *_: ui.spawn(["luxenos-software", "--install", "kdeconnect"]))
                vb.append(b)
                return
            fw = Gtk.Box(spacing=6)
            self.net = current_network()
            fw.append(ui.label("Firewall: phones on %s" % (self.net or "no local network found"), None))
            allow = Gtk.Button(label="Allow on this network")
            allow.set_sensitive(self.net is not None)
            allow.connect("clicked", lambda *_: self.firewall(["allow-phone", self.net]))
            block = Gtk.Button(label="Block")
            block.connect("clicked", lambda *_: self.firewall(["deny-phone"]))
            fw.append(allow)
            fw.append(block)
            vb.append(fw)
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class("boxed-list")
            sw = Gtk.ScrolledWindow(vexpand=True)
            sw.set_child(self.list)
            vb.append(sw)
            self.status = ui.label("", "lx-small", wrap=True)
            vb.append(self.status)
            self.refresh(True)
            GLib.timeout_add_seconds(10, lambda: self.get_visible() and (self.refresh() or True))

        def refresh(self, discover=False):
            def work():
                GLib.idle_add(self.show, devices(discover))
            threading.Thread(target=work, daemon=True).start()

        def show(self, devs):
            self.list.remove_all()
            if not devs:
                self.list.append(ui.label("No phone found. Open KDE Connect on the phone, check that both are on "
                                          "the same network, and that phone connections are allowed above.",
                                          "lx-dim", wrap=True))
            for d in devs:
                self.list.append(self.device_row(d))
            return False

        def device_row(self, d):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for m in ("start", "end", "top", "bottom"):
                getattr(box, "set_margin_" + m)(8)
            state = ("paired" if d["paired"] else "not paired") + (", connected" if d["reachable"] else ", offline")
            box.append(ui.label("%s — %s" % (d["name"], state), "lx-heading"))
            row = Gtk.Box(spacing=6)
            did = d["id"]

            def act(label, args, title):
                b = Gtk.Button(label=label)
                b.connect("clicked", lambda *_: self.run(args + ["-d", did], title))
                row.append(b)
                return b

            if not d["paired"]:
                act("Pair", ["--pair"], "Pairing request sent; accept it on the phone")
            else:
                for b in (act("Ping", ["--ping"], "Ping sent"), act("Ring phone", ["--ring"], "Ringing"),
                          self.button(row, "Send file…", lambda: self.send_file(did)),
                          self.button(row, "Send clipboard", lambda: self.send_clipboard(did)),
                          self.button(row, "Notifications", lambda: self.notifications(did))):
                    b.set_sensitive(d["reachable"])
                unpair = Gtk.Button(label="Unpair…")
                unpair.connect("clicked", lambda *_: ui.alert(
                    self, "Unpair %s?" % d["name"], "It can no longer connect until you pair again.",
                    buttons=("Cancel", "Unpair"), default=0, cancel=0,
                    callback=lambda i: i == 1 and self.run(["--unpair", "-d", did], "Unpaired")))
                row.append(unpair)
            box.append(row)
            return box

        def button(self, row, label, cb):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda *_: cb())
            row.append(b)
            return b

        def run(self, args, title):
            def work():
                rc, out = cli(*args, timeout=30)
                GLib.idle_add(done, rc, out)

            def done(rc, out):
                self.status.set_text(title if rc == 0 else "Failed: %s" % (out.splitlines()[-1] if out else rc))
                self.refresh()
                return False
            threading.Thread(target=work, daemon=True).start()

        def send_file(self, did):
            dialog = Gtk.FileDialog(title="Send a file to the phone")

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                if f is not None and f.get_path():
                    self.run(["--share", f.get_path()], "Sending %s" % os.path.basename(f.get_path()))
            dialog.open(self, None, done)

        def send_clipboard(self, did):
            clip = Gdk.Display.get_default().get_clipboard()

            def got(c, res):
                try:
                    text = c.read_text_finish(res)
                except GLib.Error:
                    text = None
                if not text:
                    self.status.set_text("The clipboard has no text.")
                    return
                # KDE Connect reads the clipboard itself; --share-text sends it explicitly.
                self.run(["--share-text", text[:100000]], "Clipboard sent")
            clip.read_text_async(None, got)

        def notifications(self, did):
            rc, out = cli("--list-notifications", "-d", did)
            ui.alert(self, "Phone notifications", out if rc == 0 and out else "No notifications from the phone "
                     "(enable notification sync in the KDE Connect app).")

        def firewall(self, args):
            if not os.access(FIREWALL, os.X_OK):
                ui.alert(self, "Unavailable", "%s is missing." % FIREWALL)
                return
            msgs = []

            def done(rc):
                if rc in (126, 127) and not msgs:
                    self.status.set_text("Authentication was cancelled; the firewall was not changed.")
                else:
                    self.status.set_text(msgs[-1] if msgs else "Firewall: exit %d" % rc)
                self.refresh(True)
            ui.run_streaming(["pkexec", FIREWALL] + args,
                             lambda l: msgs.append(l.split(" ", 1)[1] if l.startswith("@@") else l), done)

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv if argv is not None else sys.argv[:1])
