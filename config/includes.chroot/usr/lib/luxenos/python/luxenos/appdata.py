"""AppStream metadata for the Software Center (apps, descriptions, screenshots).

Debian publishes AppStream (DEP-11) metadata next to its package lists; apt
downloads it with `apt update` (package "appstream") and the AppStream
library reads it (gir1.2-appstream-1.0). This module turns that into a
{package name: App} map for Debian packages, plus helpers for screenshots.
If the library or the metadata is missing, everything degrades to the plain
package list the Software Center showed before.

The AppStream ID (e.g. org.gimp.GIMP) is also what Flathub uses, which is
how the Software Center links a Debian package and the same app on Flathub
instead of listing it twice.
"""

import hashlib
import html
import os
import re
import sys
import urllib.request

MAX_SCREENSHOT = 4 * 1024 * 1024


class App:
    __slots__ = ("id", "pkgname", "name", "summary", "description", "screenshots", "categories", "icon")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def markup_to_text(markup):
    """AppStream description markup (<p>, <ul><li>) -> plain text."""
    if not markup:
        return ""
    t = re.sub(r"\s*<li>\s*", "\n • ", markup)
    t = re.sub(r"\s*</p>\s*", "\n\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _items(container):
    """AppStream 0.16 returns GPtrArray (list), 1.0 a ComponentBox / array wrapper."""
    if container is None:
        return []
    if isinstance(container, (list, tuple)):
        return list(container)
    for attr in ("as_array", "get_entries"):
        if hasattr(container, attr):
            try:
                return list(getattr(container, attr)())
            except TypeError:
                pass
    if hasattr(container, "get_size") and hasattr(container, "index_safe"):
        return [container.index_safe(i) for i in range(container.get_size())]
    return list(container)


def _screenshot_urls(comp):
    urls = []
    getter = getattr(comp, "get_screenshots_all", None) or getattr(comp, "get_screenshots", None)
    for shot in _items(getter() if getter else None)[:4]:
        images = getattr(shot, "get_images_all", None) or getattr(shot, "get_images", None)
        best = None
        for img in _items(images() if images else None):
            url = img.get_url() if hasattr(img, "get_url") else None
            width = img.get_width() if hasattr(img, "get_width") else 0
            if url and url.startswith("https://") and (best is None or abs(width - 752) < abs(best[0] - 752)):
                best = (width, url)
        if best:
            urls.append(best[1])
    return urls


def load():
    """{pkgname: App} for desktop applications (blocking; run in a thread)."""
    try:
        import gi
        gi.require_version("AppStream", "1.0")
        from gi.repository import AppStream
    except (ImportError, ValueError):
        return {}
    try:
        pool = AppStream.Pool()
        pool.load(None)
        comps = _items(pool.get_components())
    except Exception as e:  # GLib.Error from broken metadata: no AppStream, not a crash
        print("luxenos-software: AppStream metadata unavailable: %s" % e, file=sys.stderr)
        return {}
    desktop = getattr(AppStream.ComponentKind, "DESKTOP_APP", None)
    out = {}
    for c in comps:
        try:
            if desktop is not None and c.get_kind() != desktop:
                continue
            names = c.get_pkgnames() if hasattr(c, "get_pkgnames") else [c.get_pkgname()]
            if not names or not names[0]:
                continue
            icon = None
            for i in _items(c.get_icons() if hasattr(c, "get_icons") else None):
                if i.get_kind() == AppStream.IconKind.STOCK:
                    icon = i.get_name()
                    break
            app = App(id=c.get_id(), pkgname=names[0], name=c.get_name() or names[0], summary=c.get_summary() or "",
                      description=markup_to_text(c.get_description() or ""), screenshots=_screenshot_urls(c),
                      categories=list(c.get_categories() or []), icon=icon)
        except Exception:  # one odd component must not hide the rest
            continue
        out.setdefault(app.pkgname, app)
    return out


def by_id(apps):
    return {a.id: a for a in apps.values() if a.id}


def strip_desktop(app_id):
    return app_id[:-8] if app_id and app_id.endswith(".desktop") else app_id


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "luxenos", "screenshots")


def screenshot(url):
    """Download (once) and return a local path, or None. Blocking; https only."""
    if not url or not url.startswith("https://"):
        return None
    d = cache_dir()
    path = os.path.join(d, hashlib.sha256(url.encode()).hexdigest()[:32] + os.path.splitext(url)[1][:5])
    if os.path.exists(path):
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LuxenOS-Software/1"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (https checked above)
            data = r.read(MAX_SCREENSHOT + 1)
    except (OSError, ValueError):
        return None
    if len(data) > MAX_SCREENSHOT or not data[:8].startswith((b"\x89PNG", b"\xff\xd8")):
        return None
    os.makedirs(d, exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    return path
