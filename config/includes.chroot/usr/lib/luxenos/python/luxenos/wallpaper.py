"""Wallpaper engine: one wallpaper, per monitor, per workspace, or a slideshow.

Drawn by swaybg through Sway's own `output NAME bg FILE MODE` command (works
with SwayFX and stock Sway). Settings live in ~/.config/luxenos/wallpaper.conf:

  WALLPAPER=/path/image.png | #rrggbb      default for every monitor
  WALLPAPER_MODE=fill|fit|center|tile|stretch
  WALLPAPER_OUTPUT_<NAME>=/path            one monitor (<NAME>: eDP-1 -> EDP_1)
  WALLPAPER_WS_<N>=/path                   workspace number N (shown on the
                                           monitor that shows the workspace)
  SLIDESHOW=yes|no                         change the default wallpaper
  SLIDESHOW_DIR=/path                      images from this folder
  SLIDESHOW_MINUTES=30                     every N minutes (5-1440)
  SLIDESHOW_SHUFFLE=yes|no

The static part (default + per monitor) is written to the managed Sway
fragment ~/.config/luxenos/sway.d/50-wallpaper.conf, so it is there at
login without anything running. Only per-workspace wallpapers and the
slideshow need `luxenos-wallpaper daemon` (started by luxenos-session-init,
and only when one of them is configured); it listens to Sway workspace
events and exits when there is nothing dynamic to do.

Theme packs (luxenos.themes) set WALLPAPER through this module, so there is
one wallpaper setting, not two.

Usage: luxenos-wallpaper set FILE|#rrggbb [--output NAME|--workspace N]
       luxenos-wallpaper unset --output NAME|--workspace N
       luxenos-wallpaper slideshow on DIR [MINUTES] | off
       luxenos-wallpaper next | list | apply | daemon
"""

import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time

from . import config

DEFAULT = "/usr/share/backgrounds/luxenos/wallpaper.png"
MODES = ("fill", "fit", "center", "tile", "stretch")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SYSTEM_DIRS = ("/usr/share/backgrounds", "/usr/share/wallpapers")
USAGE = __doc__[__doc__.index("Usage:"):]


def conf_path():
    return os.path.join(config.luxenos_config_dir(), "wallpaper.conf")


def fragment_path():
    return os.path.join(config.luxenos_config_dir(), "sway.d", "50-wallpaper.conf")


def user_dirs():
    home = os.path.expanduser("~")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [os.path.join(home, "Pictures", "Wallpapers"), os.path.join(data, "backgrounds")]


def output_key(name):
    return "WALLPAPER_OUTPUT_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


def valid_image(path):
    return (isinstance(path, str) and path.startswith("/") and "\n" not in path and '"' not in path
            and path.lower().endswith(IMAGE_EXT) and os.path.isfile(path))


def valid_value(v):
    return bool(HEX_RE.match(v or "")) or valid_image(v)


def load():
    return config.read_kv(conf_path())


def mode(values):
    return config.get_choice(values, "WALLPAPER_MODE", "fill", MODES)


def bg_args(value, how):
    """Sway `output X bg ...` arguments, or None for an invalid value."""
    if HEX_RE.match(value or ""):
        return '%s solid_color' % value
    if valid_image(value):
        return '"%s" %s' % (value, how)
    return None


def static_commands(values):
    """Commands for the default and per-monitor wallpapers (no workspaces)."""
    how = mode(values)
    default = values.get("WALLPAPER") if valid_value(values.get("WALLPAPER")) else DEFAULT
    cmds = ["output * bg %s" % (bg_args(default, how) or '"%s" fill' % DEFAULT)]
    for k, v in sorted(values.items()):
        if k.startswith("WALLPAPER_OUTPUT_") and valid_value(v):
            name = values.get(k + "_NAME", "")
            if re.match(r"^[A-Za-z0-9._-]{1,40}$", name):
                cmds.append("output %s bg %s" % (name, bg_args(v, how)))
    return cmds


def workspace_wallpapers(values):
    out = {}
    for k, v in values.items():
        m = re.match(r"^WALLPAPER_WS_([0-9]{1,3})$", k)
        if m and valid_value(v):
            out[int(m.group(1))] = v
    return out


def dynamic(values):
    return bool(workspace_wallpapers(values)) or config.get_bool(values, "SLIDESHOW", False)


def write_fragment(values):
    path = fragment_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# Managed by luxenos-wallpaper (Personalization > Wallpaper). Changes are overwritten.\n")
        f.write("\n".join(static_commands(values)) + "\n")
    os.replace(tmp, path)


def swaymsg(cmd):
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        subprocess.run(["swaymsg", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                       check=False)


def apply(values=None):
    values = load() if values is None else values
    write_fragment(values)
    for cmd in static_commands(values):
        swaymsg(cmd)


def update(changes, remove=()):
    for k, v in changes.items():
        if v is not None and k.startswith("WALLPAPER") and not k.endswith("_NAME") and k != "WALLPAPER_MODE" \
                and not valid_value(v):
            raise ValueError("not an image file or #rrggbb colour: %s" % v)
    config.update_kv(conf_path(), changes, header="# LuxenOS wallpaper settings (luxenos-wallpaper).",
                     remove=remove)
    apply()


def set_default(value):
    """Used by theme packs: None restores the LuxenOS default."""
    update({"WALLPAPER": value if value else None})


def images_in(directory, limit=2000):
    out = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files):
            if f.lower().endswith(IMAGE_EXT) and not f.startswith("."):
                out.append(os.path.join(root, f))
                if len(out) >= limit:
                    return out
    return out


def available():
    """Every wallpaper in the system and user folders."""
    seen, out = set(), []
    for d in list(SYSTEM_DIRS) + user_dirs():
        for p in images_in(d):
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def next_slide(values, current=None):
    imgs = images_in(values.get("SLIDESHOW_DIR", "")) if values.get("SLIDESHOW_DIR") else []
    if not imgs:
        return None
    if config.get_bool(values, "SLIDESHOW_SHUFFLE", True):
        choices = [i for i in imgs if i != current] or imgs
        return random.choice(choices)
    if current in imgs:
        return imgs[(imgs.index(current) + 1) % len(imgs)]
    return imgs[0]


# --- daemon -----------------------------------------------------------------------

def daemon():
    from . import swayipc
    values = load()
    if not dynamic(values):
        return 0
    state = {"slide": values.get("WALLPAPER")}
    lock = threading.Lock()

    def show_for_outputs():
        values = load()
        ws_map = workspace_wallpapers(values)
        how = mode(values)
        try:
            with swayipc.Connection() as c:
                outputs = c.get_outputs()
        except swayipc.IPCError:
            return
        default = state["slide"] if config.get_bool(values, "SLIDESHOW", False) and state["slide"] else None
        for o in outputs:
            if not o.get("active"):
                continue
            name = o.get("name")
            ws = o.get("current_workspace") or ""
            num = int(ws) if str(ws).isdigit() else None
            per_output = values.get(output_key(name))
            value = ws_map.get(num) or (per_output if valid_value(per_output) else None) or default or \
                (values.get("WALLPAPER") if valid_value(values.get("WALLPAPER")) else DEFAULT)
            args = bg_args(value, how)
            if args:
                swaymsg("output %s bg %s" % (name, args))

    def slideshow():
        while True:
            values = load()
            if not config.get_bool(values, "SLIDESHOW", False):
                time.sleep(60)
                continue
            minutes = config.get_int(values, "SLIDESHOW_MINUTES", 30, 5, 1440)
            nxt = next_slide(values, state["slide"])
            if nxt:
                with lock:
                    state["slide"] = nxt
                    show_for_outputs()
            time.sleep(minutes * 60)

    threading.Thread(target=slideshow, daemon=True).start()
    show_for_outputs()
    try:
        for kind, data in swayipc.events(["workspace", "output"]):
            if kind == "output" or data.get("change") in ("focus", "init", "move"):
                with lock:
                    show_for_outputs()
    except swayipc.IPCError:
        return 0  # Sway exited
    return 0


def is_running_daemon():
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "luxenos-wallpaper daemon"],
                             capture_output=True, text=True, timeout=5, check=False).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    return any(int(p) != os.getpid() for p in out if p.isdigit())


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv)[1:]
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "set" and args:
            value, target = args[0], args[1:]
            if value.startswith("/") or value.startswith("#"):
                value = os.path.realpath(value) if value.startswith("/") else value
            if target[:1] == ["--output"] and len(target) == 2:
                update({output_key(target[1]): value, output_key(target[1]) + "_NAME": target[1]})
            elif target[:1] == ["--workspace"] and len(target) == 2 and target[1].isdigit():
                update({"WALLPAPER_WS_%d" % int(target[1]): value})
            elif not target:
                update({"WALLPAPER": value})
            else:
                print(USAGE, file=sys.stderr)
                return 2
        elif cmd == "unset" and len(args) == 2:
            if args[0] == "--output":
                update({}, remove=(output_key(args[1]), output_key(args[1]) + "_NAME"))
            elif args[0] == "--workspace" and args[1].isdigit():
                update({}, remove=("WALLPAPER_WS_%d" % int(args[1]),))
            else:
                return 2
        elif cmd == "slideshow" and args[:1] == ["on"] and len(args) >= 2:
            d = os.path.realpath(args[1])
            if not images_in(d):
                raise ValueError("no images in %s" % d)
            ch = {"SLIDESHOW": "yes", "SLIDESHOW_DIR": d}
            if len(args) > 2:
                ch["SLIDESHOW_MINUTES"] = str(int(args[2]))
            config.update_kv(conf_path(), ch, header="# LuxenOS wallpaper settings (luxenos-wallpaper).")
        elif cmd == "slideshow" and args == ["off"]:
            config.update_kv(conf_path(), {"SLIDESHOW": "no"})
            apply()
        elif cmd == "next":
            values = load()
            nxt = next_slide(values, values.get("WALLPAPER"))
            if not nxt:
                raise ValueError("no slideshow folder configured")
            update({"WALLPAPER": nxt})
        elif cmd == "list":
            for p in available():
                print(p)
        elif cmd == "apply":
            apply()
        elif cmd == "daemon":
            if is_running_daemon():
                return 0
            return daemon()
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except (ValueError, OSError) as e:
        print("luxenos-wallpaper: %s" % e, file=sys.stderr)
        return 1
    if cmd in ("set", "unset", "slideshow") and dynamic(load()) and not is_running_daemon():
        subprocess.Popen(["luxenos-wallpaper", "daemon"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True) if shutil.which("luxenos-wallpaper") \
            else None
    return 0
