"""Theme packs: complete appearance presets on top of Liquid Glass.

A pack is a small KEY=VALUE file (/usr/share/luxenos/themes/*.theme, or the
user's own in ~/.local/share/luxenos/themes/). Applying one only goes through
existing mechanisms:

  * colours, glass level, GTK/Qt/Waybar/notification/lock-screen styling and
    SwayFX effects: `luxenos-glass` (writes ~/.config/luxenos/glass.conf, the
    same per-user file Settings > Accessibility writes)
  * wallpaper: a managed Sway fragment, ~/.config/luxenos/sway.d/50-wallpaper.conf
    (included by the default Sway config), applied live over IPC

The user's Liquid Glass intensity and the Accessibility choices are kept
unless the user asks for the pack's glass level (--with-glass); Reduce motion
is never touched. Before every apply the previous state is saved, so
`luxenos-theme revert` (and the Settings preview timer) can undo it.

Usage: luxenos-theme list | current | show ID | validate FILE |
       apply ID [--with-glass] | revert
"""

import os
import re
import shutil
import subprocess
import sys

from . import config

USAGE = """usage: luxenos-theme list | current | show ID | validate FILE
       luxenos-theme apply ID [--with-glass]   (--with-glass: also use the pack's glass level)
       luxenos-theme revert                     (back to the appearance before the last apply)"""

SYSTEM_DIR = os.environ.get("LUXENOS_THEMES_DIR", "/usr/share/luxenos/themes")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9/._-]+\.(png|jpg|jpeg)$")
WALLPAPER_DIRS = ("/usr/share/backgrounds/", "/usr/share/wallpapers/")

ALLOWED = {
    "THEME_NAME": lambda v: 0 < len(v) <= 40,
    "THEME_DESCRIPTION": lambda v: len(v) <= 200,
    "GLASS_PRESET": lambda v: v in ("performance", "balanced", "rich", "flat"),
    "GLASS_THEME": lambda v: v in ("dark", "light"),
    "GLASS_ACCENT": lambda v: bool(HEX_RE.match(v)),
    "GLASS_TINT_DARK": lambda v: bool(HEX_RE.match(v)),
    "GLASS_TINT_LIGHT": lambda v: bool(HEX_RE.match(v)),
    "WALLPAPER": lambda v: bool(PATH_RE.match(v)) and ".." not in v,
    "WALLPAPER_COLOR": lambda v: bool(HEX_RE.match(v)),
}
GLASS_KEYS = ("GLASS_THEME", "GLASS_ACCENT", "GLASS_TINT_DARK", "GLASS_TINT_LIGHT")


def user_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "luxenos", "themes")


def cfg_dir():
    return config.luxenos_config_dir()


def fragment_path():
    return os.path.join(cfg_dir(), "sway.d", "50-wallpaper.conf")


def backup_dir():
    return os.path.join(cfg_dir(), "theme-previous")


def validate(values, user_owned=False):
    """-> list of problems (empty = valid)."""
    errors = []
    for k, v in values.items():
        check = ALLOWED.get(k)
        if check is None:
            errors.append("unknown key %s" % k)
        elif not check(v):
            errors.append("invalid value for %s: %r" % (k, v))
    if "THEME_NAME" not in values:
        errors.append("THEME_NAME is required")
    if "WALLPAPER" in values and "WALLPAPER_COLOR" in values:
        errors.append("use WALLPAPER or WALLPAPER_COLOR, not both")
    wp = values.get("WALLPAPER")
    if wp and ALLOWED["WALLPAPER"](wp):
        home = os.path.expanduser("~") + "/"
        if not (wp.startswith(WALLPAPER_DIRS) or (user_owned and wp.startswith(home))):
            errors.append("wallpaper must be under /usr/share/backgrounds (or your home for your own packs)")
        elif not os.path.isfile(wp):
            errors.append("wallpaper not found: %s" % wp)
    return errors


def load(path, user_owned=False):
    with open(path, encoding="utf-8") as f:
        values = config.parse_kv(f.read())
    return values, validate(values, user_owned)


def available():
    """{id: (values, path)} of valid packs; user packs override system ones."""
    packs = {}
    for d, owned in ((SYSTEM_DIR, False), (user_dir(), True)):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            ident = n[:-6] if n.endswith(".theme") else None
            if not ident or not ID_RE.match(ident):
                continue
            try:
                values, errors = load(os.path.join(d, n), owned)
            except (OSError, UnicodeDecodeError):
                continue
            if errors:
                print("luxenos-theme: ignoring %s: %s" % (n, "; ".join(errors)), file=sys.stderr)
                continue
            packs[ident] = (values, os.path.join(d, n))
    return packs


def current():
    return config.read_kv(os.path.join(cfg_dir(), "theme.conf")).get("CURRENT_THEME", "")


def wallpaper_command(values):
    if values.get("WALLPAPER"):
        return "output * bg %s fill" % values["WALLPAPER"]
    if values.get("WALLPAPER_COLOR"):
        return "output * bg %s solid_color" % values["WALLPAPER_COLOR"]
    return None


def _swaymsg(cmd):
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        subprocess.run(["swaymsg", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=False)


def _glass(*args):
    exe = shutil.which("luxenos-glass")
    if not exe:
        raise RuntimeError("luxenos-glass is not installed")
    p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=120, check=False)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip() or "luxenos-glass failed")


def save_previous():
    b = backup_dir()
    os.makedirs(b, exist_ok=True)
    for name, src in (("glass.conf", os.path.join(cfg_dir(), "glass.conf")),
                      ("50-wallpaper.conf", fragment_path()),
                      ("theme.conf", os.path.join(cfg_dir(), "theme.conf"))):
        dst = os.path.join(b, name)
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.unlink(dst)
        # Marker: the file did not exist before, so revert removes it.
        absent = dst + ".absent"
        if os.path.exists(src):
            if os.path.exists(absent):
                os.unlink(absent)
        else:
            open(absent, "w").close()


def write_fragment(cmd):
    path = fragment_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if cmd is None:
        if os.path.exists(path):
            os.unlink(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# Managed by luxenos-theme (Settings > Appearance). Changes are overwritten.\n%s\n" % cmd)
    os.replace(tmp, path)


def apply(ident, with_glass=False):
    packs = available()
    if ident not in packs:
        raise KeyError("no valid theme pack named %r" % ident)
    values, _ = packs[ident]
    save_previous()
    if with_glass and values.get("GLASS_PRESET"):
        _glass("preset", values["GLASS_PRESET"])
    pairs = ["%s=%s" % (k, values[k]) for k in GLASS_KEYS if k in values]
    if pairs:
        _glass("set-many", *pairs)
    elif not with_glass:
        _glass("apply")
    cmd = wallpaper_command(values)
    write_fragment(cmd)
    _swaymsg(cmd or "output * bg /usr/share/backgrounds/luxenos/wallpaper.png fill")
    config.update_kv(os.path.join(cfg_dir(), "theme.conf"), {"CURRENT_THEME": ident},
                     header="# Managed by luxenos-theme.")


def revert():
    b = backup_dir()
    if not os.path.isdir(b):
        raise RuntimeError("there is no previous appearance to go back to")
    for name, dst in (("glass.conf", os.path.join(cfg_dir(), "glass.conf")),
                      ("50-wallpaper.conf", fragment_path()),
                      ("theme.conf", os.path.join(cfg_dir(), "theme.conf"))):
        src = os.path.join(b, name)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        elif os.path.exists(src + ".absent") and os.path.exists(dst):
            os.unlink(dst)
    _glass("apply")
    cmd = None
    try:
        with open(fragment_path(), encoding="utf-8") as f:
            cmd = next((l.strip() for l in f if l.startswith("output ")), None)
    except OSError:
        pass
    _swaymsg(cmd or "output * bg /usr/share/backgrounds/luxenos/wallpaper.png fill")


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    cmd = argv[0]
    try:
        if cmd == "list":
            cur = current()
            for ident, (values, _p) in sorted(available().items()):
                print("%s%s\t%s\t%s" % ("*" if ident == cur else " ", ident, values["THEME_NAME"],
                                        values.get("THEME_DESCRIPTION", "")))
        elif cmd == "current":
            print(current() or "(none)")
        elif cmd == "show" and len(argv) == 2:
            values, path = available()[argv[1]]
            print("# %s" % path)
            for k, v in values.items():
                print("%s=%s" % (k, v))
        elif cmd == "validate" and len(argv) == 2:
            _values, errors = load(argv[1], user_owned=True)
            for e in errors:
                print("invalid: %s" % e)
            return 1 if errors else 0
        elif cmd == "apply" and len(argv) in (2, 3):
            with_glass = "--with-glass" in argv[2:]
            apply(argv[1], with_glass)
            print("applied %s" % argv[1])
        elif cmd == "revert" and len(argv) == 1:
            revert()
            print("previous appearance restored")
        else:
            print("luxenos-theme: bad arguments (try --help)", file=sys.stderr)
            return 2
    except (KeyError, RuntimeError, OSError) as e:
        print("luxenos-theme: %s" % (e.args[0] if e.args else e), file=sys.stderr)
        return 1
    return 0
