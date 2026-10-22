"""Installed applications: discovery, search ranking and window matching.

Applications are always discovered from their desktop entries (Gio reads
every XDG data dir and honours NoDisplay/Hidden/OnlyShowIn/TryExec), never
from a hard-coded list. The ranking and matching functions are pure so they
are unit tested without GTK.
"""

import json
import os
import re
import time
import unicodedata

_WORD_SPLIT = re.compile(r"[\s\-_.:/]+")


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


class AppEntry:
    """A desktop entry reduced to what search and the dock need."""

    __slots__ = ("app_id", "name", "generic", "keywords", "description", "exec_name",
                 "wm_class", "categories", "info", "visible")

    def __init__(self, app_id, name, generic="", keywords=(), description="", exec_name="",
                 wm_class="", categories="", info=None, visible=True):
        self.app_id = app_id
        self.name = name or app_id
        self.generic = generic or ""
        self.keywords = tuple(keywords or ())
        self.description = description or ""
        self.exec_name = exec_name or ""
        self.wm_class = wm_class or ""
        self.categories = categories or ""
        self.info = info
        self.visible = visible

    def __repr__(self):
        return "AppEntry(%r)" % self.app_id


def score(query, entry, usage=0):
    """0 = no match. Every query word must match somewhere."""
    q = normalize(query)
    if not q:
        return 0
    name = normalize(entry.name)
    name_words = [w for w in _WORD_SPLIT.split(name) if w]
    generic = normalize(entry.generic)
    keywords = [normalize(k) for k in entry.keywords]
    desc = normalize(entry.description)
    exe = normalize(entry.exec_name)
    total = 0
    if name == q:
        total += 200
    elif name.startswith(q):
        total += 120
    for token in q.split():
        best = 0
        if any(w.startswith(token) for w in name_words):
            best = 80
        elif token in name:
            best = 55
        elif exe.startswith(token):
            best = 45
        elif any(k.startswith(token) for k in keywords) or generic.startswith(token) \
                or any(w.startswith(token) for w in _WORD_SPLIT.split(generic)):
            best = 40
        elif token in generic or any(token in k for k in keywords):
            best = 25
        elif token in desc:
            best = 12
        if best == 0:
            return 0
        total += best
    # Frequently launched apps float up, but never above a better text match.
    return total + min(usage, 20)


def rank(query, entries, usage=None, limit=8):
    usage = usage or {}
    scored = []
    for e in entries:
        s = score(query, e, usage.get(e.app_id, 0))
        if s:
            scored.append((-s, normalize(e.name), e))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _, _, e in scored[:limit]]


# --- window -> desktop entry matching (dock) ----------------------------------

def _base(app_id):
    a = app_id[:-8] if app_id.endswith(".desktop") else app_id
    return a.lower()


class AppIndex:
    """Find the desktop entry for a running window's app_id / WM class."""

    def __init__(self, entries):
        self.by_id = {}
        self.by_tail = {}
        self.by_wm = {}
        self.by_exec = {}
        # Visible entries are registered last so they win name clashes with
        # hidden ones (e.g. thunar.desktop hidden behind "Files").
        for e in sorted(entries, key=lambda x: x.visible):
            key = _base(e.app_id)
            self.by_id[key] = e
            self.by_tail[key.rsplit(".", 1)[-1]] = e
            if e.wm_class:
                self.by_wm[e.wm_class.lower()] = e
            if e.exec_name:
                self.by_exec[e.exec_name.lower()] = e

    def lookup(self, window_key):
        if not window_key:
            return None
        k = window_key.lower()
        for table in (self.by_id, self.by_wm, self.by_exec):
            if k in table:
                return table[k]
        tail = k.rsplit(".", 1)[-1]
        return self.by_tail.get(k) or self.by_tail.get(tail) or self.by_exec.get(tail)


# --- Gio-backed discovery -----------------------------------------------------

def exec_basename(commandline):
    """First real program of an Exec line ('env FOO=1 prog %U' -> 'prog')."""
    if not commandline:
        return ""
    parts = commandline.split()
    i = 0
    if parts and os.path.basename(parts[0]) == "env":
        i = 1
        while i < len(parts) and ("=" in parts[i] or parts[i].startswith("-")):
            i += 1
    if i < len(parts) and os.path.basename(parts[i]) in ("sh", "bash", "python3") and i + 1 < len(parts):
        i += 1
    return os.path.basename(parts[i]) if i < len(parts) else ""


def load_apps(include_hidden=False):
    """All installed applications as AppEntry objects (needs PyGObject)."""
    from gi.repository import Gio
    entries = []
    for info in Gio.AppInfo.get_all():
        visible = info.should_show()
        if not visible and not include_hidden:
            continue
        app_id = info.get_id() or ""
        generic = keywords = wm = categories = ""
        if isinstance(info, Gio.DesktopAppInfo):
            generic = info.get_generic_name() or ""
            keywords = info.get_keywords() or ()
            wm = info.get_startup_wm_class() or ""
            categories = info.get_categories() or ""
        entries.append(AppEntry(
            app_id=app_id,
            name=info.get_display_name() or info.get_name() or app_id,
            generic=generic,
            keywords=keywords,
            description=info.get_description() or "",
            exec_name=exec_basename(info.get_commandline() or ""),
            wm_class=wm,
            categories=categories,
            info=info,
            visible=visible,
        ))
    return entries


# --- launch counts (used to rank the launcher) --------------------------------

def _state_file():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "luxenos", "launcher-usage.json")


def load_usage():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}
    except (OSError, ValueError, AttributeError):
        return {}


def record_launch(app_id):
    usage = load_usage()
    usage[app_id] = usage.get(app_id, 0) + 1
    if len(usage) > 300:
        usage = dict(sorted(usage.items(), key=lambda kv: -kv[1])[:200])
    path = _state_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.%d" % (path, os.getpid(), int(time.time()))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(usage, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return usage
