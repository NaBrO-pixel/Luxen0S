"""What the Update Manager reads: pending updates and their history.

Everything here is unprivileged. Pending Debian updates come from apt's own
simulation (`apt-get -s --with-new-pkgs upgrade`, the same plan
luxenos-pkg-root later runs), Flatpak updates from `flatpak remote-ls
--updates`, firmware from fwupd (`fwupdmgr get-updates --json`), history from
apt's /var/log/apt/history.log. Installing is always done by
/usr/libexec/luxenos/luxenos-pkg-root through pkexec, one operation at a time.

The parsers are pure functions so they are unit tested without apt.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import time

from . import config

UPDATER_CONF = os.environ.get("LUXENOS_UPDATER_CONF", "/etc/luxenos/updater.conf")
APT_HISTORY = "/var/log/apt/history.log"
LISTS_DIR = "/var/lib/apt/lists"
PKG_LOG = "/var/log/luxenos/pkg-helper.log"
UPDATES_LOG = "/var/log/luxenos-updates.log"

# Settings the Update Manager shows (and luxenos-pkg-root set-update-schedule
# accepts). (key, label, kind, default)
SCHEDULE_SETTINGS = (
    ("ENABLE_AUTO_UPDATES", "Install updates automatically", "bool", "no"),
    ("AUTO_UPDATE_TIME", "Time of day", "time", "02:00"),
    ("DOWNLOAD_ONLY", "Only download automatically, install when I choose", "bool", "no"),
    ("AUTO_INSTALL_SECURITY", "Always install security updates", "bool", "yes"),
    ("UPDATE_FLATPAKS", "Include Flatpak applications", "bool", "yes"),
    ("UPDATE_FIRMWARE", "Include firmware", "bool", "yes"),
    ("UPDATE_WAYDROID", "Include Android (Waydroid)", "bool", "yes"),
    ("UPDATES_ON_AC_ONLY", "Only when plugged in", "bool", "yes"),
    ("CREATE_BACKUP_BEFORE_UPDATE", "Restore point before every update", "bool", "yes"),
    ("SNAPSHOT_BEFORE_UPDATE", "Btrfs snapshot before every update (Btrfs only)", "bool", "yes"),
    ("ENABLE_NOTIFICATIONS", "Notify me about updates", "bool", "yes"),
    ("NOTIFY_AVAILABLE", "Notify when updates are available", "bool", "yes"),
)
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class Update:
    __slots__ = ("source", "name", "old", "new", "security", "detail")

    def __init__(self, source, name, old="", new="", security=False, detail=""):
        self.source = source      # "debian" | "flatpak" | "firmware" | "android"
        self.name = name
        self.old = old
        self.new = new
        self.security = security
        self.detail = detail

    def __repr__(self):
        return "Update(%s:%s %s->%s%s)" % (self.source, self.name, self.old, self.new,
                                           " security" if self.security else "")


# --- Debian (apt) ------------------------------------------------------------------

_INST_RE = re.compile(r"^Inst (\S+)(?: \[([^\]]*)\])? \((\S+) ([^)]*)\)")


def parse_apt_upgrades(text):
    """apt-get -s output -> ([Update], [new package names], [removed], [errors]).

    'Inst libc6 [2.41-11] (2.41-12 Debian-Security:13/stable-security [amd64])'
    is an upgrade of libc6 from the security archive; an Inst line without the
    bracketed old version is a new package pulled in (for example a kernel).
    """
    upgrades, new, removed, errors = [], [], [], []
    for line in text.splitlines():
        if line.startswith("Inst "):
            m = _INST_RE.match(line)
            if not m:
                continue
            name, old, ver, origin = m.groups()
            if old is None:
                new.append(name)
                continue
            security = "-security" in origin.lower() or "Security" in origin
            source = "android" if name == "waydroid" else "debian"
            upgrades.append(Update(source, name, old, ver, security, origin.split(" [")[0]))
        elif line.startswith("Remv "):
            removed.append(line.split()[1])
        elif line.startswith("E: "):
            errors.append(line[3:])
    return upgrades, new, removed, errors


def apt_upgrades():
    """-> (updates, new, removed, errors). Blocking; run in a worker thread."""
    env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
    try:
        p = subprocess.run(["apt-get", "-s", "-q", "--with-new-pkgs", "upgrade"], capture_output=True,
                           text=True, timeout=180, env=env, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return [], [], [], ["apt-get could not be run: %s" % e]
    upgrades, new, removed, errors = parse_apt_upgrades(p.stdout + "\n" + p.stderr)
    if p.returncode != 0 and not errors:
        tail = (p.stderr or p.stdout).strip().splitlines()
        errors.append(tail[-1] if tail else "apt-get failed")
    return upgrades, new, removed, errors


def lists_age(lists_dir=LISTS_DIR, now=None):
    """Seconds since the package lists were last downloaded (None: never)."""
    files = glob.glob(os.path.join(lists_dir, "*Release")) + glob.glob(os.path.join(lists_dir, "*InRelease"))
    if not files:
        return None
    newest = max(os.path.getmtime(f) for f in files)
    return max(0, (now or time.time()) - newest)


def age_text(seconds):
    if seconds is None:
        return "never"
    if seconds < 3600:
        return "%d minutes ago" % max(1, seconds // 60)
    if seconds < 86400 * 2:
        return "%d hours ago" % (seconds // 3600)
    return "%d days ago" % (seconds // 86400)


# --- Flatpak -----------------------------------------------------------------------

def parse_flatpak_updates(text, installation):
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip() or parts[0].startswith("Application"):
            continue
        app = parts[0].strip()
        version = parts[1].strip() if len(parts) > 1 else ""
        origin = parts[2].strip() if len(parts) > 2 else ""
        out.append(Update("flatpak", app, "", version, False, "%s (%s)" % (origin or "flatpak", installation)))
    return out


def flatpak_updates():
    """-> (updates, error or None). Both system and per-user installations."""
    if not shutil.which("flatpak"):
        return [], "Flatpak is not installed"
    out, errors = [], []
    for inst in ("--system", "--user"):
        try:
            p = subprocess.run(["flatpak", "remote-ls", inst, "--updates", "--columns=application,version,origin"],
                               capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(str(e))
            continue
        if p.returncode != 0:
            msg = (p.stderr or "").strip().splitlines()
            if msg and "No remote" not in msg[-1]:
                errors.append(msg[-1])
            continue
        out += parse_flatpak_updates(p.stdout, inst[2:])
    return out, ("; ".join(errors) if errors and not out else None)


# --- firmware (fwupd) ----------------------------------------------------------------

def parse_fwupd_updates(text):
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    out = []
    for dev in data.get("Devices", []):
        rels = dev.get("Releases") or []
        if not rels:
            continue
        rel = rels[0]
        out.append(Update("firmware", dev.get("Name") or dev.get("DeviceId", "device"),
                          dev.get("Version", ""), rel.get("Version", ""), False,
                          (rel.get("Summary") or "").strip()))
    return out


def firmware_updates():
    """-> (updates, error or None). fwupdmgr exits 2 when there is nothing to do."""
    if not shutil.which("fwupdmgr"):
        return [], "fwupd is not installed"
    try:
        p = subprocess.run(["fwupdmgr", "get-updates", "--json"], capture_output=True, text=True,
                           timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return [], str(e)
    if p.returncode == 2:
        return [], None
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip().splitlines()
        return [], msg[-1] if msg else "fwupd reported an error"
    return parse_fwupd_updates(p.stdout), None


# --- history -------------------------------------------------------------------------

def parse_apt_history(text, limit=50):
    """/var/log/apt/history.log -> [{'date','command','upgraded','installed','removed'}], newest first."""
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("Start-Date:"):
            cur = {"date": line.split(":", 1)[1].strip(), "command": "", "upgraded": 0,
                   "installed": 0, "removed": 0, "error": ""}
            entries.append(cur)
        elif cur is None:
            continue
        elif line.startswith("Commandline:"):
            cur["command"] = line.split(":", 1)[1].strip()
        elif line.startswith(("Upgrade:", "Install:", "Remove:", "Purge:")):
            key = {"Upgrade": "upgraded", "Install": "installed", "Remove": "removed", "Purge": "removed"}[
                line.split(":", 1)[0]]
            # "pkg:amd64 (1.0, 1.1), other:amd64 (2.0)": count the top-level entries.
            body, depth, n = line.split(":", 1)[1], 0, 1
            for ch in body:
                depth += ch == "("
                depth -= ch == ")"
                n += ch == "," and depth == 0
            cur[key] += n if body.strip() else 0
        elif line.startswith("Error:"):
            cur["error"] = line.split(":", 1)[1].strip()
    return list(reversed(entries))[:limit]


def apt_history(limit=50):
    texts = []
    for path in sorted(glob.glob(APT_HISTORY + "*"), reverse=True):
        if path.endswith(".gz"):
            continue  # older, rotated history: compressed, rarely needed
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
        except OSError:
            continue
    return parse_apt_history("\n".join(reversed(texts)), limit)


def log_tail(path, lines=200):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-lines:]
    except OSError as e:
        return ["(%s cannot be read: %s)" % (path, e.strerror)]


# --- settings -----------------------------------------------------------------------

def schedule():
    values = config.read_kv(UPDATER_CONF)
    out = {}
    for key, _label, kind, default in SCHEDULE_SETTINGS:
        v = values.get(key, default)
        if kind == "bool":
            v = "yes" if config.get_bool(values, key, default == "yes") else "no"
        elif kind == "time" and not TIME_RE.match(v):
            v = default
        out[key] = v
    return out


def schedule_changes(current, wanted):
    """['KEY=VALUE'...] for luxenos-pkg-root set-update-schedule; validated."""
    out = []
    kinds = {k: kind for k, _l, kind, _d in SCHEDULE_SETTINGS}
    for k, v in wanted.items():
        if k not in kinds:
            raise ValueError("unknown setting %s" % k)
        if kinds[k] == "bool" and v not in ("yes", "no"):
            raise ValueError("%s must be yes or no" % k)
        if kinds[k] == "time" and not TIME_RE.match(v):
            raise ValueError("%s must be HH:MM" % k)
        if current.get(k) != v:
            out.append("%s=%s" % (k, v))
    return out


def notify_summary(upgrades):
    """Text of the 'updates available' notification, or None."""
    if not upgrades:
        return None
    sec = sum(1 for u in upgrades if u.security)
    text = "%d update%s available" % (len(upgrades), "" if len(upgrades) == 1 else "s")
    if sec:
        text += ", %d of them security update%s" % (sec, "" if sec == 1 else "s")
    return text
