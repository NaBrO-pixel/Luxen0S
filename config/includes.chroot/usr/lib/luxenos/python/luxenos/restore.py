"""Restore points as listed by the Restore Center (read-only, unprivileged).

The data is written by /usr/libexec/luxenos/luxenos-restore-root: each
restore point is a directory with a world-readable info.conf (or, for points
made by older updaters, backup-metadata.txt) next to a root-only archive of
/etc. Everything that changes the system goes through that helper.
"""

import os
import re
import subprocess
import time

from . import config

BACKUP_DIR = os.environ.get("LUXENOS_RESTORE_DIR", "/var/backups/luxenos")
ROOT_HELPER = "/usr/libexec/luxenos/luxenos-restore-root"
ID_RE = re.compile(r"^luxenos-backup-(\d{8})-(\d{6})(-\d+)?$")

COVERS = ("System settings in /etc (network, services, display manager, apt sources, …)",
          "The list of installed packages: removed ones are installed again, newer ones removed")
NOT_COVERED = ("Your files and personal settings in /home (never read or changed)",
               "Program files and package versions (packages come back in their current version)",
               "User accounts and passwords, the machine id, disk layout (fstab, crypttab)",
               "Kernels and the core desktop packages (never removed by a restore)")


class RestorePoint:
    def __init__(self, ident, created, description, trigger, legacy, info):
        self.id = ident
        self.created = created
        self.description = description
        self.trigger = trigger
        self.legacy = legacy
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


def parse_point(ident, info_text, legacy_text=None):
    """RestorePoint from info.conf text (FORMAT=2) or legacy metadata."""
    if info_text is not None:
        info = config.parse_kv(info_text)
        if info.get("FORMAT") != "2":
            return None
        try:
            created = int(info.get("CREATED", ""))
        except ValueError:
            created = id_time(ident)
        trigger = info.get("TRIGGER", "manual")
        if trigger not in ("manual", "update", "restore"):
            trigger = "manual"
        return RestorePoint(ident, created, info.get("DESCRIPTION") or "Restore point", trigger, False, info)
    if legacy_text is not None:
        return RestorePoint(ident, id_time(ident), "Backup made by the updater (older format)", "update", True,
                            {"DETAILS": legacy_text.strip()})
    return None


def list_points(backup_dir=BACKUP_DIR):
    points = []
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return points
    for name in names:
        if not ID_RE.match(name):
            continue
        d = os.path.join(backup_dir, name)
        if os.path.islink(d) or not os.path.isdir(d):
            continue
        info = legacy = None
        try:
            with open(os.path.join(d, "info.conf"), encoding="utf-8") as f:
                info = f.read()
        except OSError:
            try:
                with open(os.path.join(d, "backup-metadata.txt"), encoding="utf-8") as f:
                    legacy = f.read()
            except OSError:
                continue
        p = parse_point(name, info, legacy)
        if p is not None:
            points.append(p)
    points.sort(key=lambda p: p.id, reverse=True)
    return points


def root_fs():
    try:
        out = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "/"], capture_output=True, text=True,
                             timeout=5, check=False).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def snapshot_note(fstype):
    if fstype == "btrfs":
        return ("The system disk uses Btrfs, which can take snapshots, but LuxenOS does not manage Btrfs "
                "snapshots yet. Restore points here are configuration backups, not snapshots.")
    return ("The system disk uses %s, which has no snapshots. Restore points are configuration backups "
            "(/etc and the package list), not complete copies of the system." % fstype)


def parse_item(line):
    """'@@ITEM section text' -> (section, text) or None."""
    if not line.startswith("@@ITEM "):
        return None
    rest = line[7:]
    section, _, text = rest.partition(" ")
    return section, text
