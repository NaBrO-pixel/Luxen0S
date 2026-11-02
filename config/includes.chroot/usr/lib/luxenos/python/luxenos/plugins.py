"""LuxenOS plugins: discovery, metadata, enable/disable, sandboxed execution.

A plugin is a directory with a plugin.conf (KEY=VALUE, parsed never sourced)
and one executable:

  /usr/share/luxenos/plugins/<id>/            shipped with LuxenOS (enabled by default)
  ~/.local/share/luxenos/plugins/<id>/        your own (disabled until you enable them)

  PLUGIN_ID=unicode-chars          [a-z0-9-], must equal the directory name
  PLUGIN_NAME=Unicode characters
  PLUGIN_VERSION=1.0
  PLUGIN_API=1                     plugin API this was written for (LuxenOS speaks API 1)
  PLUGIN_TYPE=search               the only type so far: launcher search provider
  PLUGIN_EXEC=search               file in the plugin directory
  PLUGIN_PERMISSIONS=              space separated: network, home-read
  PLUGIN_DESCRIPTION=...
  PLUGIN_AUTHOR=...
  PLUGIN_TIMEOUT_MS=1500           at most 3000

Safety: plugins are never imported into a LuxenOS process and never run as
root. Each search runs the plugin's executable as the user inside a
bubblewrap sandbox: read-only /usr, its own directory read-only at /plugin,
an empty /tmp, no network and no access to your files unless the plugin
declares (and you accepted, by enabling it) "network" or "home-read". Without
bubblewrap no plugin runs. Output is limited in size and time.

Protocol: the executable gets the query as its only argument and prints one
JSON object: {"results": [{"title": "...", "subtitle": "...", "icon":
"icon-name", "copy": "text to copy", "open": "https://..."}]} (at most 8).

Enabled state: ~/.config/luxenos/plugins.conf, PLUGIN_<ID>=yes|no.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import threading

from . import config

API_VERSION = 1
SYSTEM_DIR = os.environ.get("LUXENOS_PLUGINS_DIR", "/usr/share/luxenos/plugins")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~-]{0,20}$")
PERMISSIONS = {
    "network": "Use the internet",
    "home-read": "Read the files in your home folder",
}
MAX_OUTPUT = 64 * 1024
MAX_RESULTS = 8


def user_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "luxenos", "plugins")


def state_path():
    return os.path.join(config.luxenos_config_dir(), "plugins.conf")


def state_key(ident):
    return "PLUGIN_" + ident.upper().replace("-", "_")


class Plugin:
    def __init__(self, ident, directory, values, system):
        self.id = ident
        self.dir = directory
        self.values = values
        self.system = system
        self.problems = []

    name = property(lambda self: self.values.get("PLUGIN_NAME", self.id))
    version = property(lambda self: self.values.get("PLUGIN_VERSION", "?"))
    description = property(lambda self: self.values.get("PLUGIN_DESCRIPTION", ""))
    author = property(lambda self: self.values.get("PLUGIN_AUTHOR", ""))
    kind = property(lambda self: self.values.get("PLUGIN_TYPE", ""))

    @property
    def permissions(self):
        return [p for p in self.values.get("PLUGIN_PERMISSIONS", "").split() if p]

    @property
    def executable(self):
        return os.path.join(self.dir, self.values.get("PLUGIN_EXEC", ""))

    @property
    def timeout(self):
        return config.get_int(self.values, "PLUGIN_TIMEOUT_MS", 1500, 100, 3000) / 1000.0

    @property
    def usable(self):
        return not self.problems


def validate(ident, directory, values, system, uid=None):
    """-> list of problems (empty = loadable)."""
    problems = []
    if values.get("PLUGIN_ID") != ident:
        problems.append("PLUGIN_ID does not match the folder name")
    if not values.get("PLUGIN_NAME"):
        problems.append("PLUGIN_NAME is missing")
    if not VERSION_RE.match(values.get("PLUGIN_VERSION", "")):
        problems.append("PLUGIN_VERSION is missing or invalid")
    try:
        api = int(values.get("PLUGIN_API", ""))
    except ValueError:
        api = None
    if api != API_VERSION:
        problems.append("written for plugin API %s; this LuxenOS supports API %d" % (
            values.get("PLUGIN_API", "?"), API_VERSION))
    if values.get("PLUGIN_TYPE") != "search":
        problems.append("unsupported PLUGIN_TYPE %r" % values.get("PLUGIN_TYPE", ""))
    unknown = [p for p in values.get("PLUGIN_PERMISSIONS", "").split() if p not in PERMISSIONS]
    if unknown:
        problems.append("unknown permission(s): %s" % ", ".join(unknown))
    exe_name = values.get("PLUGIN_EXEC", "")
    if not exe_name or "/" in exe_name or exe_name.startswith("."):
        problems.append("PLUGIN_EXEC must be a file name inside the plugin folder")
    else:
        exe = os.path.join(directory, exe_name)
        try:
            st = os.lstat(exe)
        except OSError:
            problems.append("%s is missing" % exe_name)
        else:
            if not stat.S_ISREG(st.st_mode) or not st.st_mode & stat.S_IXUSR:
                problems.append("%s is not an executable file" % exe_name)
    if not system:
        # Your own plugins must be yours and not writable by anyone else.
        uid = os.getuid() if uid is None else uid
        for path in (directory, os.path.join(directory, exe_name or "plugin.conf")):
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if st.st_uid != uid or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or stat.S_ISLNK(st.st_mode):
                problems.append("%s must belong to you and not be writable by others" % os.path.basename(path))
    return problems


def discover():
    """{id: Plugin}; your own plugins override shipped ones with the same id."""
    found = {}
    for base, system in ((SYSTEM_DIR, True), (user_dir(), False)):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for n in names:
            d = os.path.join(base, n)
            if not ID_RE.match(n) or os.path.islink(d) or not os.path.isdir(d):
                continue
            values = config.read_kv(os.path.join(d, "plugin.conf"))
            p = Plugin(n, d, values, system)
            p.problems = validate(n, d, values, system)
            found[n] = p
    return found


def enabled(plugin, state=None):
    state = config.read_kv(state_path()) if state is None else state
    return config.get_bool(state, state_key(plugin.id), plugin.system)


def set_enabled(ident, on):
    if not ID_RE.match(ident):
        raise ValueError("invalid plugin id")
    config.update_kv(state_path(), {state_key(ident): "yes" if on else "no"},
                     header="# LuxenOS plugins: PLUGIN_<ID>=yes|no (luxenos-plugins).")


def sandbox_argv(plugin, query, home=None):
    """bubblewrap command line for one search (None if bubblewrap is missing)."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return None
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", plugin.dir, "/plugin",
            "--setenv", "LANG", "C.UTF-8", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp"]
    if "network" in plugin.permissions:
        argv.insert(2, "--share-net")
        for f in ("/etc/resolv.conf", "/etc/ssl", "/etc/ca-certificates", "/etc/hosts", "/etc/nsswitch.conf"):
            if os.path.exists(f):
                argv += ["--ro-bind", f, f]
    if "home-read" in plugin.permissions:
        h = home or os.path.expanduser("~")
        argv += ["--ro-bind", h, h, "--setenv", "LUXENOS_HOME", h]
    argv += ["--chdir", "/plugin", "/plugin/" + os.path.basename(plugin.executable), "--", query]
    return argv


def _clip(value, n):
    return str(value)[:n] if value is not None else None


def parse_output(text, plugin_name):
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for r in (data.get("results") or [])[:50] if isinstance(data, dict) else []:
        if len(out) >= MAX_RESULTS:
            break
        if not isinstance(r, dict) or not r.get("title"):
            continue
        item = {"plugin": plugin_name, "title": _clip(r["title"], 200), "subtitle": _clip(r.get("subtitle"), 200),
                "icon": _clip(r.get("icon"), 80), "action": {}}
        if r.get("copy") is not None:
            item["action"]["copy"] = _clip(r["copy"], 4096)
        if isinstance(r.get("open"), str) and r["open"].startswith("https://"):
            item["action"]["open"] = r["open"][:2048]
        out.append(item)
    return out


def run_search(plugin, query):
    if os.getuid() == 0:
        return []  # never as root
    argv = sandbox_argv(plugin, query)
    if argv is None:
        return []
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=plugin.timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    return parse_output(p.stdout[:MAX_OUTPUT].decode("utf-8", "replace"), plugin.name)


def search_all(query):
    """Results of every enabled, usable search plugin (run in parallel)."""
    q = query.strip()
    if not q or len(q) > 200:
        return []
    state = config.read_kv(state_path())
    active = [p for p in discover().values() if p.usable and p.kind == "search" and enabled(p, state)]
    results, lock, threads = [], threading.Lock(), []

    def work(p):
        found = run_search(p, q)
        with lock:
            results.extend(found)

    for p in active:
        t = threading.Thread(target=work, args=(p,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(3.5)
    return results[:MAX_RESULTS * 2]
