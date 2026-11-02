"""Install themes from files: GTK, icon and cursor themes, wallpapers and
LuxenOS Liquid Glass presets (theme packs).

Everything is installed for the current user only, never system-wide and
never as root:

  GTK theme          ~/.local/share/themes/<name>/
  icon/cursor theme  ~/.local/share/icons/<name>/
  wallpapers         ~/.local/share/backgrounds/luxenos/
  glass preset       ~/.local/share/luxenos/themes/<id>.theme  (luxenos.themes)

Validation before anything is written: archives (.tar.gz/.tar.xz/.tar.bz2/
.tar.zst is not supported by Python, .zip) are inspected entry by entry and
refused if any entry has an absolute path, "..", a link, a device or FIFO,
or if they hold more than MAX_FILES files or MAX_BYTES of data. Executable
bits are dropped; themes are data. The type is detected from the content
(index.theme, gtk-3.0/, cursors/, images). An existing theme of the same
name is only replaced when asked (replace=True), and then atomically.

Debian's own theme packages are installed through the Software Center
instead (apt verifies them); this module is for files you downloaded.
"""

import configparser
import os
import re
import shutil
import tarfile
import tempfile
import zipfile

from . import themes

MAX_FILES = 20000
MAX_BYTES = 300 * 1024 * 1024
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")


class InstallError(Exception):
    pass


def data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def targets():
    d = data_home()
    return {"gtk": os.path.join(d, "themes"), "icons": os.path.join(d, "icons"),
            "cursors": os.path.join(d, "icons"), "wallpapers": os.path.join(d, "backgrounds", "luxenos"),
            "glass": themes.user_dir()}


def safe_member_name(name):
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\0" in name:
        return False
    parts = [p for p in name.split("/") if p not in ("", ".")]
    return bool(parts) and ".." not in parts


# --- reading archives ------------------------------------------------------------------

def list_archive(path):
    """-> [(name, size, is_dir)] after checking every entry; raises InstallError."""
    entries, total = [], 0
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                mode = (info.external_attr >> 16) & 0o170000
                if mode and mode not in (0o100000, 0o040000):
                    raise InstallError("archive contains a link or special file: %s" % info.filename)
                entries.append((info.filename.rstrip("/"), info.file_size, info.is_dir()))
                total += info.file_size
    else:
        try:
            with tarfile.open(path) as t:
                for m in t:
                    if not (m.isfile() or m.isdir()):
                        raise InstallError("archive contains a link or special file: %s" % m.name)
                    entries.append((m.name.rstrip("/"), m.size, m.isdir()))
                    total += m.size
                    if len(entries) > MAX_FILES:
                        break
        except tarfile.TarError as e:
            raise InstallError("not a supported archive (.tar.gz, .tar.xz, .tar.bz2, .zip): %s" % e)
    if len(entries) > MAX_FILES:
        raise InstallError("archive has more than %d files" % MAX_FILES)
    if total > MAX_BYTES:
        raise InstallError("archive is larger than %d MB unpacked" % (MAX_BYTES // 2 ** 20))
    for name, _s, _d in entries:
        if not safe_member_name(name):
            raise InstallError("archive contains an unsafe path: %r" % name)
    return entries


def extract(path, dest):
    """Extract a checked archive into dest (files 0644, folders 0755)."""
    list_archive(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                target = os.path.join(dest, *[p for p in info.filename.split("/") if p not in ("", ".")])
                if info.is_dir():
                    os.makedirs(target, mode=0o755, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o644)
    else:
        with tarfile.open(path) as t:
            for m in t:
                target = os.path.join(dest, *[p for p in m.name.split("/") if p not in ("", ".")])
                if m.isdir():
                    os.makedirs(target, mode=0o755, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
                src = t.extractfile(m)
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o644)


# --- detection -------------------------------------------------------------------------

def read_index(path):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    return cp


def classify_dir(d):
    """A folder that is one theme -> 'gtk' | 'icons' | 'cursors' | None."""
    idx = read_index(os.path.join(d, "index.theme")) if os.path.isfile(os.path.join(d, "index.theme")) else None
    if idx is not None and idx.has_section("Icon Theme") and idx.get("Icon Theme", "Directories", fallback=""):
        return "icons"
    if os.path.isdir(os.path.join(d, "cursors")):
        return "cursors"
    if any(os.path.isdir(os.path.join(d, x)) for x in ("gtk-3.0", "gtk-4.0", "gtk-2.0")):
        return "gtk"
    return None


def detect(root):
    """[(kind, source path, name)] of everything installable under root."""
    found = []
    candidates = [root] + [os.path.join(root, n) for n in sorted(os.listdir(root))
                           if os.path.isdir(os.path.join(root, n))]
    for d in candidates:
        kind = classify_dir(d)
        if kind:
            found.append((kind, d, os.path.basename(d.rstrip("/"))))
    if not found:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if f.endswith(".theme") and not f.startswith("."):
                    found.append(("glass", p, f[:-6]))
                elif f.lower().endswith(IMAGE_EXT) and not f.startswith("."):
                    found.append(("wallpapers", p, f))
    return found


# --- installing ------------------------------------------------------------------------

def install_dir(kind, src, name, replace=False):
    if not NAME_RE.match(name):
        raise InstallError("unsuitable theme name: %r" % name)
    parent = targets()[kind]
    os.makedirs(parent, exist_ok=True)
    dest = os.path.join(parent, name)
    if os.path.lexists(dest) and not replace:
        raise InstallError("%s is already installed (choose Replace to overwrite it)" % name)
    tmp = tempfile.mkdtemp(prefix=".install-", dir=parent)
    try:
        staged = os.path.join(tmp, name)
        shutil.copytree(src, staged, symlinks=False)
        for dirpath, dirs, files in os.walk(staged):
            os.chmod(dirpath, 0o755)
            for f in files:
                os.chmod(os.path.join(dirpath, f), 0o644)
        if os.path.lexists(dest):
            old = dest + ".old-install"
            if os.path.lexists(old):
                shutil.rmtree(old)
            os.rename(dest, old)
            os.rename(staged, dest)
            shutil.rmtree(old)
        else:
            os.rename(staged, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def install_glass(src, ident, replace=False):
    ident = ident.lower()
    if not themes.ID_RE.match(ident):
        raise InstallError("preset file name must be lowercase letters, digits and dashes: %s.theme" % ident)
    with open(src, encoding="utf-8", errors="replace") as f:
        values = themes.config.parse_kv(f.read(64 * 1024))
    errors = themes.validate(values, user_owned=True)
    if errors:
        raise InstallError("invalid Liquid Glass preset: %s" % "; ".join(errors))
    parent = targets()["glass"]
    os.makedirs(parent, exist_ok=True)
    dest = os.path.join(parent, ident + ".theme")
    if os.path.exists(dest) and not replace:
        raise InstallError("a preset named %s already exists" % ident)
    shutil.copyfile(src, dest + ".tmp")
    os.chmod(dest + ".tmp", 0o644)
    os.replace(dest + ".tmp", dest)
    return dest


def install_wallpaper(src, name, replace=False):
    parent = targets()["wallpapers"]
    os.makedirs(parent, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "wallpaper.png"
    dest = os.path.join(parent, base)
    if os.path.exists(dest) and not replace:
        return dest  # same file name: keep the existing one
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o644)
    return dest


def install(path, replace=False):
    """Install everything found in a file (archive, .theme or image). -> [(kind, dest)]"""
    if not os.path.isfile(path):
        raise InstallError("not a file: %s" % path)
    lower = path.lower()
    if lower.endswith(".theme"):
        return [("glass", install_glass(path, os.path.basename(path)[:-6], replace))]
    if lower.endswith(IMAGE_EXT):
        return [("wallpapers", install_wallpaper(path, os.path.basename(path), replace))]
    work = tempfile.mkdtemp(prefix="luxenos-theme-")
    try:
        extract(path, work)
        items = detect(work)
        if not items:
            raise InstallError("no GTK, icon or cursor theme, wallpaper or Liquid Glass preset found in this file")
        done = []
        for kind, src, name in items:
            if kind in ("gtk", "icons", "cursors"):
                done.append((kind, install_dir(kind, src, name, replace)))
            elif kind == "glass":
                done.append((kind, install_glass(src, name, replace)))
            else:
                done.append((kind, install_wallpaper(src, name, replace)))
        return done
    finally:
        shutil.rmtree(work, ignore_errors=True)


def installed(kind):
    """Names of installed icon ('icons') or cursor ('cursors') themes, system and user."""
    out = set()
    dirs = ["/usr/share/icons", os.path.join(data_home(), "icons"), os.path.expanduser("~/.icons")]
    for base in dirs:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            d = os.path.join(base, n)
            if not os.path.isdir(d):
                continue
            has_cursors = os.path.isdir(os.path.join(d, "cursors"))
            if kind == "cursors" and has_cursors:
                out.add(n)
            elif kind == "icons":
                # Cursor-only themes have no icon Directories (Adwaita has both).
                idx = read_index(os.path.join(d, "index.theme")) if os.path.isfile(os.path.join(d, "index.theme")) \
                    else None
                if idx is not None and idx.has_section("Icon Theme") and \
                        idx.get("Icon Theme", "Directories", fallback="").strip() and \
                        idx.get("Icon Theme", "Hidden", fallback="false") != "true":
                    out.add(n)
    out.discard("default")
    if kind == "icons" and os.path.isdir("/usr/share/icons/Adwaita"):
        out.add("Adwaita")  # the default; it marks itself Hidden
    return sorted(out, key=str.lower)
