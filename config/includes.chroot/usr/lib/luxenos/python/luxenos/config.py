"""KEY=VALUE configuration files, in the same format as /etc/luxenos/glass.conf.

One setting per line, '#' comments, no shell syntax: files are parsed, never
sourced. Unknown keys are kept when a file is rewritten, so a user's own
lines and comments survive.
"""

import os
import re
import tempfile

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


def luxenos_config_dir():
    return os.path.join(config_home(), "luxenos")


def parse_kv(text):
    """Return {KEY: VALUE} for every well-formed line. Later lines win."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_kv(path):
    try:
        with open(path, encoding="utf-8") as f:
            return parse_kv(f.read())
    except (OSError, UnicodeDecodeError):
        return {}


def read_layered(system_path, user_path):
    """System defaults overridden by the per-user file."""
    values = read_kv(system_path)
    values.update(read_kv(user_path))
    return values


def _check_value(key, value):
    if not KEY_RE.match(key):
        raise ValueError("invalid key: %r" % key)
    value = str(value)
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("invalid value for %s" % key)
    return value


def update_kv(path, updates, header=None, remove=()):
    """Set (or with value None / listed in remove, delete) keys in path.

    Other lines are preserved. The file is replaced atomically so a crash
    never leaves a half-written configuration.
    """
    updates = {k: (None if v is None else _check_value(k, v)) for k, v in updates.items()}
    for k in remove:
        updates[k] = None
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = [header] if header else []
    out = []
    done = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            if key not in done and updates[key] is not None:
                out.append("%s=%s" % (key, updates[key]))
            done.add(key)
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in done and value is not None:
            out.append("%s=%s" % (key, value))
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- typed accessors ----------------------------------------------------------

def get_bool(values, key, default):
    v = values.get(key, "").lower()
    if v in ("yes", "true", "1", "on"):
        return True
    if v in ("no", "false", "0", "off"):
        return False
    return default


def get_int(values, key, default, lo, hi):
    try:
        n = int(values.get(key, ""))
    except ValueError:
        return default
    return n if lo <= n <= hi else default


def get_choice(values, key, default, choices):
    v = values.get(key, "")
    return v if v in choices else default


# --- glass.conf ---------------------------------------------------------------

GLASS_SYS_CONF = "/etc/luxenos/glass.conf"


def glass_values():
    sys_conf = os.environ.get("LUXENOS_GLASS_SYS_CONF", GLASS_SYS_CONF)
    return read_layered(sys_conf, os.path.join(luxenos_config_dir(), "glass.conf"))


def reduce_motion():
    """True when the user asked for reduced motion (Settings > Accessibility)."""
    v = glass_values()
    if get_bool(v, "GLASS_REDUCE_MOTION", False):
        return True
    return get_int(v, "GLASS_ANIM_MS", 120, 0, 2000) == 0


def is_live_session(cmdline_path="/proc/cmdline"):
    """True on the live USB (installer kiosk or live desktop)."""
    try:
        with open(cmdline_path, encoding="utf-8") as f:
            return "boot=live" in f.read().split()
    except OSError:
        return False
