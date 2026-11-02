"""Btrfs snapshots as shown by the Restore Center (read-only, unprivileged).

The snapshots and their world-readable .info files are written by
/usr/libexec/luxenos/luxenos-snapshot-root; listing them needs no privileges.
Whether snapshots (and rollback) are possible at all is decided from the
filesystem, never assumed: on ext4 and everything else the page explains that
restore points are what is available.
"""

import os
import re
import subprocess
import time

from . import config

SNAPDIR = os.environ.get("LUXENOS_SNAPSHOT_DIR", "/.snapshots")
ROOT_HELPER = "/usr/libexec/luxenos/luxenos-snapshot-root"
ID_RE = re.compile(r"^luxenos-snap-(\d{8})-(\d{6})(-\d+)?$")

LIMITS = (
    "A snapshot is stored on the same disk: it does not protect against a failing or lost disk.",
    "Rolling back brings back programs and system settings exactly as they were, including package versions.",
    "Separate subvolumes or partitions (normally /home) are not part of a snapshot and never rolled back.",
    "If /var/log is part of the system subvolume, logs are rolled back too.",
    "Rollback needs /home on its own subvolume, /boot on the system subvolume and fstab mounting the "
    "system by subvol=; otherwise only creating and deleting snapshots is offered.",
    "The previous system is kept after a rollback (as NAME.before-rollback-DATE) until you remove it.",
)


class Snapshot:
    def __init__(self, ident, created, description, trigger, info):
        self.id = ident
        self.created = created
        self.description = description
        self.trigger = trigger
        self.info = info

    @property
    def created_text(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created)) if self.created else "unknown"


def id_time(ident):
    m = ID_RE.match(ident)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
    except ValueError:
        return None


def parse_info(ident, text):
    info = config.parse_kv(text or "")
    if text is not None and info.get("FORMAT") not in (None, "1"):
        return None
    try:
        created = int(info.get("CREATED", ""))
    except ValueError:
        created = id_time(ident)
    trigger = info.get("TRIGGER", "manual")
    if trigger not in ("manual", "update", "rollback"):
        trigger = "manual"
    return Snapshot(ident, created, info.get("DESCRIPTION") or "Snapshot", trigger, info)


def list_snapshots(snapdir=SNAPDIR):
    out = []
    try:
        names = os.listdir(snapdir)
    except OSError:
        return out
    for name in names:
        if not ID_RE.match(name) or not os.path.isdir(os.path.join(snapdir, name)):
            continue
        try:
            with open(os.path.join(snapdir, name + ".info"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = None
        s = parse_info(name, text)
        if s is not None:
            out.append(s)
    out.sort(key=lambda s: s.id, reverse=True)
    return out


def root_fstype():
    try:
        return subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "/"], capture_output=True, text=True,
                              timeout=5, check=False).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def parse_status(lines):
    """'@@ITEM key value' lines of `luxenos-snapshot-root status` -> dict."""
    out = {}
    for line in lines:
        if line.startswith("@@ITEM "):
            key, _, value = line[7:].partition(" ")
            out[key] = value
    return out


def rollback_verdict(status):
    """(supported, reason) from parsed status."""
    v = status.get("rollback", "")
    if v == "yes":
        return True, ""
    return False, v[3:] if v.startswith("no ") else (v or "unknown")


def over_retention(snaps, keep):
    """Automatic snapshots beyond the newest `keep` (what prune would delete)."""
    auto = [s for s in snaps if s.trigger == "update"]
    return auto[keep:]
