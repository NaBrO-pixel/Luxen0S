"""Flatpak for the Software Center: search, installed apps, install, remove.

Only the `flatpak` command is used. Installing for everyone goes into the
system installation, where Flatpak asks for authorization itself (its own
polkit actions, flatpak-system-helper); installing "just for me" needs no
password at all. Flathub is added system-wide by hook 0200. Parsers are pure.
"""

import re
import shutil
import subprocess

APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def available():
    return shutil.which("flatpak") is not None


def _run(args, timeout=60):
    try:
        p = subprocess.run(["flatpak"] + list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return p.returncode, p.stdout, p.stderr.strip()


def parse_installed(text):
    """--columns=application,name,version,origin,installation -> [dict]"""
    out = []
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) >= 5 and APP_ID_RE.match(p[0]):
            out.append({"id": p[0], "name": p[1] or p[0], "version": p[2], "origin": p[3],
                        "installation": p[4]})
    return out


def parse_search(text):
    """--columns=application,name,description,version,remotes -> [dict], one per app id."""
    out, seen = [], set()
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) >= 5 and APP_ID_RE.match(p[0]) and p[0] not in seen:
            seen.add(p[0])
            remotes = [r for r in p[4].split(",") if REMOTE_RE.match(r.strip())]
            out.append({"id": p[0], "name": p[1] or p[0], "summary": p[2], "version": p[3],
                        "remote": remotes[0].strip() if remotes else ""})
    return out


def installed():
    if not available():
        return []
    rc, out, _err = _run(["list", "--app", "--columns=application,name,version,origin,installation"])
    return parse_installed(out) if rc == 0 else []


def search(query):
    """-> (results, error). Flatpak searches its locally cached AppStream data."""
    q = query.strip()
    if not available():
        return [], "Flatpak is not installed"
    if len(q) < 2:
        return [], None
    rc, out, err = _run(["search", "--columns=application,name,description,version,remotes", "--", q])
    if rc != 0:
        return [], err or "flatpak search failed"
    return parse_search(out), None


def remotes():
    rc, out, _e = _run(["remotes", "--columns=name"])
    return [r.strip() for r in out.splitlines() if REMOTE_RE.match(r.strip())] if rc == 0 else []


def install_argv(app_id, remote, user=False):
    if not APP_ID_RE.match(app_id) or not REMOTE_RE.match(remote):
        raise ValueError("invalid Flatpak id or remote")
    return ["flatpak", "install", "--user" if user else "--system", "-y", "--noninteractive", remote, app_id]


def uninstall_argv(app_id, installation):
    if not APP_ID_RE.match(app_id):
        raise ValueError("invalid Flatpak id")
    return ["flatpak", "uninstall", "--user" if installation == "user" else "--system", "-y", "--noninteractive",
            app_id]
