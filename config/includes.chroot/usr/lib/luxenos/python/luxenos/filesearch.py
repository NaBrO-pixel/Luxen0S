"""File search for the launcher.

Prefers plocate (an index updated daily by plocate-updatedb.timer, so a
search costs milliseconds and never walks the disk). Without it, a bounded
walk of the XDG user folders is used: depth- and entry-limited, run in a
worker thread, and cached for a minute, so the launcher never stalls.
Only files under the user's home directory are returned, hidden files and
folders are skipped.
"""

import os
import shutil
import subprocess
import threading
import time

MAX_WALK_ENTRIES = 20000
MAX_DEPTH = 4
CACHE_SECONDS = 60


def home():
    return os.path.realpath(os.path.expanduser("~"))


def is_visible_path(path, home_dir):
    """Under home and no hidden component (".cache", ".local", ...)."""
    if not path.startswith(home_dir + os.sep):
        return False
    rel = path[len(home_dir) + 1:]
    return not any(part.startswith(".") for part in rel.split(os.sep))


def filter_matches(paths, query, home_dir, limit):
    """Rank candidate paths: basename prefix first, then substring."""
    q = query.lower()
    starts, contains = [], []
    for p in paths:
        if not is_visible_path(p, home_dir):
            continue
        base = os.path.basename(p).lower()
        if base.startswith(q):
            starts.append(p)
        elif q in base:
            contains.append(p)
        if len(starts) >= limit:
            break
    return (starts + contains)[:limit]


def user_dirs(home_dir):
    names = ("DESKTOP", "DOCUMENTS", "DOWNLOAD", "MUSIC", "PICTURES", "VIDEOS", "PUBLICSHARE", "TEMPLATES")
    dirs = []
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home_dir, ".config"), "user-dirs.dirs")
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                for n in names:
                    prefix = "XDG_%s_DIR=" % n
                    if line.startswith(prefix):
                        d = line[len(prefix):].strip().strip('"').replace("$HOME", home_dir)
                        dirs.append(d)
    except OSError:
        pass
    for d in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"):
        dirs.append(os.path.join(home_dir, d))
    seen, out = set(), []
    for d in dirs:
        if d not in seen and d != home_dir and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def walk_limited(roots, max_entries=MAX_WALK_ENTRIES, max_depth=MAX_DEPTH):
    found = []
    stack = [(r, 0) for r in roots]
    while stack and len(found) < max_entries:
        path, depth = stack.pop()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    found.append(entry.path)
                    if depth < max_depth and entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, depth + 1))
                    if len(found) >= max_entries:
                        break
        except OSError:
            continue
    return found


class FileSearcher:
    def __init__(self):
        self.home = home()
        self.plocate = shutil.which("plocate")
        self._cache = None
        self._cache_time = 0
        self._lock = threading.Lock()

    @property
    def backend(self):
        return "plocate" if self.plocate else "folder scan"

    def search(self, query, limit=8):
        """Blocking; call from a worker thread."""
        query = query.strip()
        if len(query) < 2:
            return []
        if self.plocate:
            try:
                # argv list, "--" ends options: the query is never parsed as a flag.
                out = subprocess.run([self.plocate, "-i", "-b", "-l", "400", "--", query],
                                     capture_output=True, text=True, timeout=3, check=False)
                if out.returncode in (0, 1):
                    paths = [p for p in out.stdout.splitlines() if p]
                    return [p for p in filter_matches(paths, query, self.home, limit * 3)
                            if os.path.exists(p)][:limit]
            except (OSError, subprocess.SubprocessError):
                pass
        with self._lock:
            if self._cache is None or time.monotonic() - self._cache_time > CACHE_SECONDS:
                self._cache = walk_limited(user_dirs(self.home))
                self._cache_time = time.monotonic()
            cache = self._cache
        return filter_matches(cache, query, self.home, limit)
