"""LuxenOS Assistant: local, optional help for everyday questions.

It answers on this computer, without an account and without any external
service: an offline engine that knows LuxenOS (settings, apps, logs) and
the common Linux commands and error messages. It can

  * explain an error message you paste (apt/dpkg, Waydroid, permissions,
    disk, network, systemd ...);
  * find a setting ("where do I change the wallpaper");
  * explain a command ("what does rm -rf do"), warning about dangerous ones;
  * give hardware troubleshooting steps (Wi-Fi, sound, Bluetooth, graphics,
    battery, Android) using the Hardware Manager's checks;
  * summarize system health (CPU, memory, disk, battery, failed services,
    pending updates);
  * explain why an update failed (reads the LuxenOS package log);
  * help with desktop tasks ("take a screenshot", "install vlc").

Safety: it never runs anything on its own. Suggested actions are buttons;
opening an app or running a read-only diagnostic command (shown in full,
from a fixed list) happens only when you press it. Commands that need an
administrator are only shown, with a Copy button, never run.

Optional local AI model (off by default): ~/.config/luxenos/assistant.conf
  ASSISTANT_BACKEND=offline|ollama
  ASSISTANT_URL=http://127.0.0.1:11434     loopback addresses only
  ASSISTANT_MODEL=llama3.2
Only your question (no system data) is sent to that local model, and only
when the offline engine has no answer.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request

from . import config, pkg, search, sysinfo, ui

APP_ID = "org.luxenos.Assistant"

# Read-only diagnostics the assistant may offer to run (after a click).
DIAGNOSTICS = {
    "failed-services": ["systemctl", "--failed", "--no-legend", "--no-pager"],
    "disk": ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"],
    "memory": ["free", "-h"],
    "network": ["ip", "-br", "address"],
    "nm": ["nmcli", "general", "status"],
    "wifi": ["nmcli", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
    "sound": ["wpctl", "status"],
    "bluetooth": ["bluetoothctl", "show"],
    "usb": ["lsusb"],
    "kernel": ["uname", "-a"],
    "android": ["waydroid", "status"],
    "user-errors": ["journalctl", "--user", "-b", "-p", "err", "-n", "30", "--no-pager"],
}


class Answer:
    def __init__(self, text, actions=(), source="offline"):
        self.text = text
        self.actions = list(actions)  # (label, kind, payload) kind: open | run | copy
        self.source = source


# --- command explanations -------------------------------------------------------------

COMMANDS = {
    "ls": "lists the files in a folder (-l details, -a also hidden files, -h readable sizes)",
    "cd": "changes the current folder",
    "pwd": "prints the current folder",
    "cp": "copies files (-r folders too)",
    "mv": "moves or renames files",
    "rm": "deletes files permanently, without a wastebasket (-r folders too, -f without asking)",
    "rmdir": "deletes an empty folder",
    "mkdir": "creates a folder (-p with its parents)",
    "cat": "prints a file's contents",
    "less": "shows a file page by page (q quits)",
    "grep": "searches text for a pattern (-r in folders, -i ignore case)",
    "find": "searches for files by name, size, date ...",
    "chmod": "changes who may read, write or run a file",
    "chown": "changes the owner of a file (needs administrator rights)",
    "sudo": "runs one command as administrator (root) after your password",
    "apt": "Debian's package manager: install, remove, update software",
    "apt-get": "Debian's package manager (script-friendly form of apt)",
    "dpkg": "the low-level Debian package tool (apt uses it)",
    "flatpak": "installs and runs Flatpak apps (e.g. from Flathub)",
    "systemctl": "controls system services (start, stop, status, enable)",
    "journalctl": "shows the system log (-b this boot, -u one service, -p err errors only)",
    "df": "shows free space on disks (-h readable sizes)",
    "du": "shows how much space folders use (-sh one total)",
    "free": "shows memory use (-h readable sizes)",
    "top": "live list of running programs (q quits); htop is a friendlier version",
    "ps": "lists running processes",
    "kill": "asks a process to stop (by its number)",
    "pkill": "stops processes by name",
    "ip": "shows and changes network addresses and routes",
    "ping": "checks whether another computer answers over the network",
    "nmcli": "NetworkManager's command line (Wi-Fi, connections)",
    "ssh": "logs in to another computer securely",
    "scp": "copies files to/from another computer over SSH",
    "curl": "downloads a URL (prints it, or -o saves it)",
    "wget": "downloads a URL to a file",
    "tar": "packs or unpacks .tar archives (-x unpack, -c create, -z gzip)",
    "unzip": "unpacks .zip archives",
    "man": "shows the manual page of a command",
    "lsblk": "lists disks and partitions",
    "mount": "attaches a filesystem to a folder",
    "umount": "detaches a mounted filesystem",
    "dd": "copies raw data between files and devices; a wrong of= erases a disk",
    "mkfs": "formats a partition (erases everything on it)",
    "passwd": "changes your password",
    "waydroid": "controls Android (session start/stop, app install/launch)",
    "wine": "runs Windows programs",
    "git": "version control for code and documents",
    "python3": "runs Python programs",
    "swaymsg": "sends commands to the Sway desktop",
    "luxenos-glass": "LuxenOS appearance tool (themes, Liquid Glass)",
    "luxenos-restore": "LuxenOS restore points from a text console",
    "luxenos-updater": "LuxenOS updater for a terminal",
    "luxenos-health-check": "checks the LuxenOS system for common problems",
}
DANGEROUS = (
    (re.compile(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b.*\s/(\s|$|\*)"), "deletes the whole system"),
    (re.compile(r"\brm\s+-[a-z]*r"), "deletes folders and everything in them, without a wastebasket"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "overwrites a whole disk or partition"),
    (re.compile(r"\bmkfs"), "erases a partition"),
    (re.compile(r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba)?sh"), "runs a script from the internet without showing it"),
    (re.compile(r"chmod\s+(-R\s+)?777"), "lets every user change these files"),
    (re.compile(r":\(\)\s*\{"), "is a fork bomb that freezes the computer"),
    (re.compile(r">\s*/dev/sd[a-z]"), "overwrites a disk"),
)


def explain_command(cmdline):
    try:
        words = shlex.split(cmdline)
    except ValueError:
        words = cmdline.split()
    if not words:
        return None
    parts, warn = [], []
    i = 0
    if words[0] == "sudo":
        parts.append("sudo: runs the rest as administrator (root)")
        i = 1
    if i < len(words):
        name = os.path.basename(words[i])
        desc = COMMANDS.get(name)
        if desc is None and shutil.which("whatis"):
            try:
                out = subprocess.run(["whatis", "--", name], capture_output=True, text=True, timeout=5,
                                     check=False).stdout.strip().splitlines()
                desc = out[0].split(" - ", 1)[1] if out and " - " in out[0] else None
            except (OSError, subprocess.SubprocessError):
                desc = None
        parts.append("%s: %s" % (name, desc or "not a command I know; try `man %s`" % name))
        flags = [w for w in words[i + 1:] if w.startswith("-")]
        if flags:
            parts.append("options: %s" % " ".join(flags))
    for rx, why in DANGEROUS:
        if rx.search(cmdline):
            warn.append("Careful: this %s." % why)
            break
    return "\n".join(parts + warn)


# --- error explanations ------------------------------------------------------------------

ERRORS = (
    (r"permission denied", "You are not allowed to do this as your normal user. If it is your own file, check "
                           "its permissions (Files › Properties). For system changes use the LuxenOS apps (they ask "
                           "for the administrator password) or prefix a terminal command with sudo."),
    (r"command not found", "That program is not installed (or the name is misspelled). Search for it in the "
                           "Software Center."),
    (r"no space left on device", "The disk is full. Delete large files or old downloads; the System Monitor "
                                 "shows which disk is full."),
    (r"read-only file system", "The disk was switched to read-only, usually because of a disk error. Save your "
                               "work, restart, and let the system check the disk."),
    (r"temporary failure in name resolution|could not resolve|name or service not known",
     "Names on the internet cannot be looked up: there is no network connection or DNS is not working. "
     "Check Wi-Fi in Quick Settings (Super+N)."),
    (r"connection refused", "Nothing is listening at that address/port, or a firewall blocks it."),
    (r"connection timed out|network is unreachable", "The network or the server cannot be reached."),
    (r"certificate|ssl|tls", "A secure connection could not be verified (wrong date/time, a captive portal, or a "
                             "bad certificate). Check the clock and the network."),
    (r"segmentation fault|core dumped", "The program crashed. Update it (Update Manager); if it keeps "
                                        "happening, report it with what you did."),
    (r"failed to start|job for .* failed", "A system service could not start. `systemctl status NAME` shows why; "
                                           "use the button to list failed services."),
    (r"killed|out of memory|oom", "The system ran out of memory and stopped a program. Close other programs; the "
                                  "System Monitor shows what uses memory."),
    (r"input/output error|i/o error", "The disk reported a read/write error. Back up your files soon and check "
                                      "the disk's health."),
)


def explain_error(text):
    low = text.lower()
    advice, _repair = pkg.diagnose([text])
    if not advice.startswith("The package manager reported an error"):
        return advice
    try:
        from . import android
        a = android.explain(text)
        if a:
            return a
    except ImportError:
        pass
    for rx, msg in ERRORS:
        if re.search(rx, low):
            return msg
    return None


# --- the engine ------------------------------------------------------------------------------

def looks_like_error(text):
    return bool(re.search(r"\b(error|failed|failure|cannot|can't|could not|unable|denied|not found|refused|"
                          r"warning)\b|(^|\s)e: ", text.lower())) and len(text) > 12


def command_question(text):
    m = re.match(r"^\s*(?:what (?:does|is)|explain|meaning of)\s+`?(.+?)`?\s*(?:do|mean)?\??\s*$", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"`([^`]+)`", text)
    return m.group(1) if m else None


def health_summary():
    lines = []
    s = sysinfo.Sampler()
    import time
    time.sleep(0.5)
    snap = s.sample()
    mem = snap["memory"] or {}
    lines.append("CPU: %.0f%% busy" % snap["cpu"])
    if mem.get("total"):
        lines.append("Memory: %s of %s used" % (ui.human_bytes(mem["used"]), ui.human_bytes(mem["total"])))
    for d in sysinfo.read_disks()[:4]:
        w = sysinfo.disk_warning(d)
        lines.append("Disk %s: %s free%s" % (d.get("mount", "?"), ui.human_bytes(d.get("free", 0)),
                                             " — almost full!" if w else ""))
    pct, text = sysinfo.battery_summary(sysinfo.read_batteries())
    if pct is not None:
        lines.append("Battery: " + text)
    try:
        failed = subprocess.run(DIAGNOSTICS["failed-services"], capture_output=True, text=True, timeout=10,
                                check=False).stdout.strip().splitlines()
        units = [w for l in failed for w in l.split()[:2] if "." in w and w != "●"]
        lines.append("Failed services: %s" % (", ".join(units) or "none"))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from . import updates
        lines.append("Package lists downloaded %s" % updates.age_text(updates.lists_age()))
    except Exception:
        pass
    if pkg.dpkg_interrupted():
        lines.append("A package operation was interrupted: open the Update Manager and use Repair.")
    return "\n".join(lines)


def update_problem():
    try:
        from . import updates
        tail = updates.log_tail(updates.PKG_LOG, 60)
    except Exception:
        tail = []
    if pkg.dpkg_interrupted():
        return Answer("A previous package operation was interrupted. The Update Manager's Repair button finishes "
                      "it (dpkg --configure -a, then apt-get -f install).", [("Open Update Manager", "open",
                                                                              ["luxenos-update-manager"])])
    failures = [l for l in tail if "FAILED" in l or "ERROR" in l or "E:" in l]
    if not failures:
        return Answer("The LuxenOS package log shows no recent failure. If an update window showed an error, "
                      "paste the message here.", [("Open Update Manager", "open", ["luxenos-update-manager"])])
    advice, _r = pkg.diagnose(tail)
    return Answer("The last update problem in the log:\n%s\n\n%s" % (failures[-1][-300:], advice),
                  [("Open Update Manager", "open", ["luxenos-update-manager"])])


HARDWARE = {
    "wifi": ("Wi-Fi", "1. Check that Wi-Fi is on (Quick Settings, Super+N) and airplane mode is off.\n"
                      "2. The Hardware Manager shows whether the Wi-Fi adapter has a driver; a missing driver "
                      "usually means a missing firmware-* package.\n3. Forget and re-join the network.",
             [("Quick Settings", "open", ["luxenos-quick-settings"]), ("Hardware Manager", "open",
                                                                       ["luxenos-hardware"]),
              ("List Wi-Fi networks", "run", "wifi")]),
    "sound": ("Sound", "1. Check the volume and mute in Quick Settings.\n2. Pick the right output device in "
                       "Sound settings (pavucontrol).\n3. `wpctl status` lists what PipeWire sees.",
              [("Sound settings", "open", ["pavucontrol-qt"]), ("Show audio devices", "run", "sound")]),
    "bluetooth": ("Bluetooth", "1. Turn Bluetooth on in Quick Settings.\n2. Put the device in pairing mode.\n"
                               "3. If no adapter is found, check the Hardware Manager's USB list.",
                  [("Quick Settings", "open", ["luxenos-quick-settings"]), ("Bluetooth status", "run",
                                                                            "bluetooth")]),
    "graphics": ("Graphics", "The Hardware Manager shows which driver your graphics chip uses. Without one the "
                             "desktop uses slow software rendering. For old GPUs choose the Performance Liquid "
                             "Glass preset.", [("Hardware Manager", "open", ["luxenos-hardware"]),
                                               ("Liquid Glass settings", "open",
                                                ["luxenos-personalize", "--page", "glass"])]),
    "battery": ("Battery", "The Hardware Manager shows battery health (wear). Power profiles (Super+P) trade "
                           "speed for battery life.", [("Hardware Manager", "open", ["luxenos-hardware"])]),
    "android": ("Android", "The Android app's Troubleshooting page checks the setup step by step.",
                [("Android", "open", ["luxenos-android"]), ("Waydroid status", "run", "android")]),
}
HW_WORDS = {"wifi": ("wifi", "wi-fi", "wireless", "internet"), "sound": ("sound", "audio", "speaker", "volume",
                                                                         "headphone", "microphone"),
            "bluetooth": ("bluetooth",), "graphics": ("graphics", "gpu", "screen", "display", "monitor", "slow"),
            "battery": ("battery", "charging", "power"), "android": ("android", "waydroid", "apk")}

TASKS = (
    (("screenshot", "screen shot"), "Print saves a screenshot to ~/Pictures and the clipboard; Super+Shift+S "
                                    "selects an area and lets you annotate it.", []),
    (("lock the screen", "lock screen", "lock my screen"), "Super+L locks the screen.", [("Lock now", "open", ["luxenos-lock"])]),
    (("terminal",), "Super+Return opens a terminal (Foot).", [("Open a terminal", "open", ["foot"])]),
    (("window", "workspace", "overview"), "Super+Tab shows all windows and workspaces; drag a window to move it.",
     [("Workspace overview", "open", ["luxenos-overview"])]),
    (("phone",), "Phone Connect pairs your phone (KDE Connect app) for files, clipboard and notifications.",
     [("Phone Connect", "open", ["luxenos-phone"])]),
    (("exe", "windows program"), "Double-click a .exe file to run it with Wine; the Wine Manager gives programs "
                                 "their own environment.", [("Wine Manager", "open", ["luxenos-wine-manager"])]),
    (("backup", "restore", "undo", "rollback"), "The Restore Center creates restore points (and, on Btrfs, "
                                                "snapshots).", [("Restore Center", "open",
                                                                 ["luxenos-restore-center"])]),
)


def answer(question, values=None):
    """The offline engine. Returns an Answer or None."""
    q = question.strip()
    low = q.lower()
    if not q:
        return Answer("Ask me about an error message, a setting, a command, your hardware or how your system is "
                      "doing.")
    # Installing software
    m = re.match(r"^(?:how (?:do i|to|can i) )?install\s+([a-z0-9][a-z0-9+.-]{1,60})\s*\??$", low)
    if m and pkg.valid_name(m.group(1)):
        name = m.group(1)
        return Answer("Open the Software Center to install “%s”; it shows exactly what will change before asking "
                      "for your password." % name, [("Install %s…" % name, "open",
                                                     ["luxenos-software", "--install", name]),
                                                    ("Software Center", "open", ["luxenos-software"])])
    # Health
    if re.search(r"\b(health|how is my (system|computer|pc)|system status|is everything ok)\b", low):
        return Answer(health_summary(), [("System Monitor", "open", ["luxenos-monitor"]),
                                         ("List failed services", "run", "failed-services")])
    # Updates
    if re.search(r"\bupdate", low) and re.search(r"fail|error|broken|stuck|problem|won't|can't", low):
        return update_problem()
    # Commands
    cmd = command_question(q)
    if cmd and len(cmd) < 300:
        exp = explain_command(cmd)
        if exp:
            return Answer(exp, [("Copy command", "copy", cmd)])
    # Error messages
    if looks_like_error(q):
        e = explain_error(q)
        if e:
            acts = [("List failed services", "run", "failed-services")] if "service" in e else []
            return Answer(e, acts)
    # Hardware
    for key, words in HW_WORDS.items():
        if any(w in low for w in words) and re.search(r"not|n't|no |problem|issue|broken|slow|help|fix|doesn", low):
            title, text, acts = HARDWARE[key]
            return Answer("%s troubleshooting:\n%s" % (title, text), acts)
    # Desktop tasks
    for words, text, acts in TASKS:
        if any(w in low for w in words):
            return Answer(text, acts)
    # Settings
    topic = re.sub(r"^(where|how) (do i|can i|to) (change|find|set|open|turn on|turn off)\s+", "", low)
    topic = " ".join(w for w in re.findall(r"[a-z0-9-]+", topic) if w not in ("the", "my", "a", "an", "of", "is"))
    found = search.match_settings(topic)
    if found:
        return Answer("You find that here:", [(t, "open", argv) for t, argv in found])
    return None


# --- optional local model ----------------------------------------------------------------------

def conf():
    return config.read_kv(os.path.join(config.luxenos_config_dir(), "assistant.conf"))


def local_model_url(values):
    if values.get("ASSISTANT_BACKEND") != "ollama":
        return None
    url = values.get("ASSISTANT_URL", "http://127.0.0.1:11434").rstrip("/")
    host = urllib.parse.urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        return None  # local-first: never a remote service
    return url


def ask_local_model(question, values):
    url = local_model_url(values)
    if not url:
        return None
    body = json.dumps({"model": values.get("ASSISTANT_MODEL", "llama3.2"), "stream": False,
                       "prompt": "You help users of LuxenOS, a Debian-based desktop with Sway. Answer briefly. "
                                 "Never tell the user to disable security features.\n\nQuestion: " + question[:2000]})
    req = urllib.request.Request(url + "/api/generate", data=body.encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (loopback only, checked above)
            data = json.loads(r.read(1024 * 1024).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    text = (data.get("response") or "").strip()
    return Answer(text, source="local model") if text else None


def respond(question):
    values = conf()
    a = answer(question, values)
    if a is not None:
        return a
    a = ask_local_model(question, values)
    if a is not None:
        return a
    return Answer("I don't know that one yet. Try describing it differently, paste the exact error message, or "
                  "search the settings with Super+Space.", [("Health check", "run", "failed-services")])


def run_diagnostic(key):
    argv = DIAGNOSTICS.get(key)
    if argv is None:
        raise ValueError("unknown diagnostic")
    if not shutil.which(argv[0]):
        return "%s is not installed." % argv[0]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    return (p.stdout + p.stderr).strip()[:6000] or "(no output)"


def main(argv=None):
    Gtk = ui.init()
    from gi.repository import Gdk, Gio, GLib

    class Window(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Assistant")
            self.set_default_size(640, 680)
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for m in ("start", "end", "top", "bottom"):
                getattr(root, "set_margin_" + m)(12)
            self.set_child(root)
            backend = "your local model as a fallback" if local_model_url(conf()) else "offline"
            root.append(ui.label("Answers come from this computer (%s). Nothing runs until you press a button." %
                                 backend, "lx-dim lx-small", wrap=True))
            self.chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.sw = Gtk.ScrolledWindow(vexpand=True)
            self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.sw.set_child(self.chat)
            root.append(self.sw)
            row = Gtk.Box(spacing=6)
            self.entry = Gtk.Entry(placeholder_text="Ask, or paste an error message", hexpand=True)
            ui.accessible_label(self.entry, "Your question")
            self.entry.connect("activate", lambda *_: self.ask())
            send = Gtk.Button(label="Ask")
            send.add_css_class("suggested-action")
            send.connect("clicked", lambda *_: self.ask())
            row.append(self.entry)
            row.append(send)
            root.append(row)
            self.add_bubble("Hello! I can explain errors and commands, find settings, check your system's health "
                            "and help with hardware problems.", False,
                            [("How is my system?", "ask", "how is my system"),
                             ("Wi-Fi not working", "ask", "wifi not working"),
                             ("Why did my update fail?", "ask", "update failed")])
            self.entry.grab_focus()

        def add_bubble(self, text, mine, actions=(), source=None):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("lx-card")
            card.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
            lbl = ui.label(text, None, wrap=True, selectable=True)
            lbl.set_max_width_chars(60)
            card.append(lbl)
            if source and source != "offline":
                card.append(ui.label("from %s" % source, "lx-dim lx-small"))
            if actions:
                fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3)
                for label, kind, payload in actions:
                    b = Gtk.Button(label=label)
                    if kind == "run":
                        b.set_tooltip_text("Runs: %s" % " ".join(DIAGNOSTICS.get(payload, [])))
                    b.connect("clicked", lambda _b, k=kind, p=payload: self.action(k, p))
                    fb.append(b)
                card.append(fb)
            self.chat.append(card)
            GLib.idle_add(self.scroll_end)
            return card

        def scroll_end(self):
            adj = self.sw.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False

        def ask(self, text=None):
            text = (text if text is not None else self.entry.get_text()).strip()
            if not text:
                return
            self.entry.set_text("")
            self.add_bubble(text, True)
            thinking = self.add_bubble("…", False)

            def work():
                a = respond(text)
                GLib.idle_add(done, a)

            def done(a):
                self.chat.remove(thinking)
                self.add_bubble(a.text, False, a.actions, a.source)
                return False
            threading.Thread(target=work, daemon=True).start()

        def action(self, kind, payload):
            if kind == "open":
                if not ui.spawn(payload):
                    self.add_bubble("%s could not be started." % payload[0], False)
            elif kind == "copy":
                Gdk.Display.get_default().get_clipboard().set(payload)
                self.add_bubble("Copied.", False)
            elif kind == "ask":
                self.ask(payload)
            elif kind == "run":
                self.add_bubble("$ " + " ".join(DIAGNOSTICS[payload]), True)

                def work():
                    out = run_diagnostic(payload)
                    GLib.idle_add(lambda: self.add_bubble(out, False) and False)
                threading.Thread(target=work, daemon=True).start()

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Gtk.Application.do_startup(self)
            ui.load_css()

        def do_activate(self):
            (self.get_active_window() or Window(self)).present()

    return App().run(argv if argv is not None else sys.argv[:1])
