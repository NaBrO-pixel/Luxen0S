"""Universal search for the launcher (Super+Space): everything except apps
and files, which stay in luxenos.apps and luxenos.filesearch.

  * calculator: arithmetic parsed with Python's ast and evaluated by a small
    whitelist interpreter (numbers, + - * / // % **, parentheses and a few
    math functions). Nothing is ever passed to eval().
  * unit conversion: "10 km to mi", "70 f in c", "2 GiB to MB", "90 min to h"
  * settings: every LuxenOS settings page, searchable by name and keywords
  * web search shortcuts: "ddg cats", "wiki Debian", "!pkg gimp" ... opened in
    the default browser; providers configurable, all can be turned off
  * recent searches: kept locally (~/.local/state/luxenos/search-history.json),
    can be cleared or switched off

Settings: ~/.config/luxenos/search.conf (KEY=VALUE)
  SEARCH_HISTORY=yes|no        remember searches (default yes)
  SEARCH_HISTORY_SIZE=20       how many
  SEARCH_WEB=yes|no            offer web search results (default yes)
  SEARCH_WEB_DEFAULT=ddg       engine for "Search the web for ..."
  SEARCH_WEB_ENGINE_<ID>=Name|https://example.org/search?q=%s   extra engines
  SEARCH_PLUGINS=yes|no        query enabled launcher plugins (default yes)

Pure functions: unit tested without GTK.
"""

import ast
import json
import math
import operator
import os
import re
import time
import urllib.parse

from . import apps as appsmod
from . import config

# --- settings ----------------------------------------------------------------------

def conf_path():
    return os.path.join(config.luxenos_config_dir(), "search.conf")


def settings():
    return config.read_kv(conf_path())


# --- calculator --------------------------------------------------------------------

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10,
          "ln": math.log, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
          "exp": math.exp}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MATHY = re.compile(r"^[\d\s.+\-*/%^()a-z,]+$")


class CalcError(ValueError):
    pass


def _eval(node, depth=0):
    if depth > 40:
        raise CalcError("expression too deep")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e6):
            raise CalcError("number too large")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        return _UNOPS[type(node.op)](_eval(node.operand, depth + 1))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS \
            and not node.keywords and 1 <= len(node.args) <= 2:
        return _FUNCS[node.func.id](*[_eval(a, depth + 1) for a in node.args])
    raise CalcError("unsupported expression")


def calculate(text):
    """'2+2*3' -> 8, or None if text is not arithmetic. '=' prefix forces it."""
    t = text.strip().lower()
    forced = t.startswith("=")
    if forced:
        t = t[1:].strip()
    if not t or len(t) > 200 or not _MATHY.match(t) or not any(c.isdigit() for c in t):
        return None
    if not forced and not re.search(r"[\d)]\s*[-+*/%^]|[a-z]+\(|^\s*-", t):
        return None  # a bare number is not a calculation
    t = t.replace("^", "**")
    try:
        value = _eval(ast.parse(t, mode="eval"))
    except (SyntaxError, CalcError, ZeroDivisionError, OverflowError, ValueError, TypeError):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def format_number(value):
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 1e15):
        return "{:,}".format(int(value)).replace(",", " ")
    return ("%.10g" % value)


# --- unit conversion ------------------------------------------------------------------

# unit -> (dimension, factor to the base unit)
UNITS = {}


def _units(dimension, table):
    for names, factor in table:
        for n in names.split():
            UNITS[n] = (dimension, factor)


_units("length", (("mm millimeter millimeters millimetre", 0.001), ("cm centimeter centimeters", 0.01),
                  ("m meter meters metre metres", 1.0), ("km kilometer kilometers kilometre", 1000.0),
                  ("in inch inches", 0.0254), ("ft foot feet", 0.3048), ("yd yard yards", 0.9144),
                  ("mi mile miles", 1609.344), ("nmi", 1852.0)))
_units("mass", (("mg", 1e-6), ("g gram grams", 0.001), ("kg kilogram kilograms kilo kilos", 1.0),
                ("t tonne tonnes", 1000.0), ("oz ounce ounces", 0.028349523125),
                ("lb lbs pound pounds", 0.45359237), ("st stone", 6.35029318)))
_units("volume", (("ml milliliter millilitre", 0.001), ("cl", 0.01), ("l liter liters litre litres", 1.0),
                  ("gal gallon gallons", 3.785411784), ("qt quart quarts", 0.946352946),
                  ("pt pint pints", 0.473176473), ("cup cups", 0.2365882365), ("floz", 0.0295735295625)))
_units("time", (("ms", 0.001), ("s sec secs second seconds", 1.0), ("min mins minute minutes", 60.0),
                ("h hr hrs hour hours", 3600.0), ("d day days", 86400.0), ("wk week weeks", 604800.0),
                ("yr year years", 31557600.0)))
_units("data", (("b byte bytes", 1.0), ("kb", 1e3), ("mb", 1e6), ("gb", 1e9), ("tb", 1e12),
                ("kib", 1024.0), ("mib", 1024.0 ** 2), ("gib", 1024.0 ** 3), ("tib", 1024.0 ** 4),
                ("bit bits", 0.125), ("mbit", 125000.0), ("gbit", 1.25e8)))
_units("speed", (("mps m/s", 1.0), ("kmh km/h kph", 1 / 3.6), ("mph", 0.44704), ("kn knot knots", 0.514444)))
_units("area", (("m2 sqm", 1.0), ("km2", 1e6), ("ha hectare hectares", 1e4), ("acre acres", 4046.8564224),
                ("ft2 sqft", 0.09290304)))
TEMPERATURE = {"c": "C", "celsius": "C", "°c": "C", "f": "F", "fahrenheit": "F", "°f": "F",
               "k": "K", "kelvin": "K"}

_CONV_RE = re.compile(r"^\s*(-?[\d.,]+)\s*([a-z°/0-9]+)\s+(?:to|in|as|=)\s+([a-z°/0-9]+)\s*$")


def _to_kelvin(v, u):
    return {"C": v + 273.15, "F": (v - 32) * 5 / 9 + 273.15, "K": v}[u]


def _from_kelvin(v, u):
    return {"C": v - 273.15, "F": (v - 273.15) * 9 / 5 + 32, "K": v}[u]


def convert(text):
    """'10 km to mi' -> (value, 'mi', '10 km') or None."""
    m = _CONV_RE.match(text.lower())
    if not m:
        return None
    shown = re.match(_CONV_RE.pattern, text, re.I)
    dst_shown = shown.group(3) if shown else m.group(3)
    src_shown = shown.group(2) if shown else m.group(2)
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    src, dst = m.group(2), m.group(3)
    if src in TEMPERATURE and dst in TEMPERATURE:
        result = _from_kelvin(_to_kelvin(value, TEMPERATURE[src]), TEMPERATURE[dst])
        return result, "°" + TEMPERATURE[dst] if TEMPERATURE[dst] != "K" else "K", m.group(1) + " " + src
    a, b = UNITS.get(src), UNITS.get(dst)
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1], dst_shown, m.group(1) + " " + src_shown


# --- settings index --------------------------------------------------------------------

# (title, keywords, argv)
SETTINGS = (
    ("Appearance", "theme dark light accent colour color wallpaper", ["luxenos-personalize"]),
    ("Liquid Glass", "blur transparency glass intensity opacity", ["luxenos-personalize", "--page", "glass"]),
    ("Wallpaper", "background slideshow picture", ["luxenos-personalize", "--page", "wallpaper"]),
    ("Fonts", "font text size typeface inter", ["luxenos-personalize", "--page", "fonts"]),
    ("Themes and icons", "icon cursor gtk theme install marketplace", ["luxenos-personalize", "--page", "themes"]),
    ("Accessibility", "reduce motion animation contrast", ["luxenos-settings", "accessibility"]),
    ("Dock", "dock panel taskbar pinned autohide", ["luxenos-settings", "dock"]),
    ("Desktop widgets", "widget weather clock calendar cpu", ["luxenos-widgets", "--settings"]),
    ("Notifications", "notification do not disturb dnd sound history", ["luxenos-notifications"]),
    ("Network", "wifi wi-fi ethernet vpn internet", ["luxenos-network"]),
    ("Sound", "audio volume speaker microphone output input", ["pavucontrol-qt"]),
    ("Software updates", "update upgrade security flatpak firmware", ["luxenos-update-manager"]),
    ("Automatic updates", "schedule automatic update time", ["luxenos-update-manager"]),
    ("Restore points and snapshots", "backup restore rollback snapshot btrfs", ["luxenos-restore-center"]),
    ("Hardware", "device driver cpu gpu battery usb firmware secure boot", ["luxenos-hardware"]),
    ("Android", "waydroid apk android apps resolution", ["luxenos-android"]),
    ("Phone", "phone kde connect pair sms clipboard", ["luxenos-phone"]),
    ("Windows programs", "wine exe prefix windows", ["luxenos-wine-manager"]),
    ("Plugins", "plugin extension add-on", ["luxenos-plugins"]),
    ("Keyboard and time zone", "keyboard layout timezone language welcome", ["luxenos-welcome"]),
    ("Software", "install apps programs store flatpak", ["luxenos-software"]),
    ("Assistant", "help assistant troubleshoot explain", ["luxenos-assistant"]),
)


def match_settings(query, entries=SETTINGS, limit=4):
    q = appsmod.normalize(query)
    if not q:
        return []
    scored = []
    for title, words, argv in entries:
        hay = appsmod.normalize(title + " " + words)
        tokens = q.split()
        if all(t in hay for t in tokens):
            s = 2 if appsmod.normalize(title).startswith(q) else 1
            scored.append((-s, title, argv))
        elif all(appsmod.fuzzy_ratio(t, hay) for t in tokens if len(t) >= 3) and any(len(t) >= 3 for t in tokens):
            scored.append((0, title, argv))
    scored.sort()
    return [(t, a) for _s, t, a in scored[:limit]]


# --- web search ------------------------------------------------------------------------

ENGINES = {
    "ddg": ("DuckDuckGo", "https://duckduckgo.com/?q=%s"),
    "wiki": ("Wikipedia", "https://en.wikipedia.org/w/index.php?search=%s"),
    "pkg": ("Debian packages", "https://packages.debian.org/search?keywords=%s"),
    "bug": ("Debian bugs", "https://bugs.debian.org/cgi-bin/pkgreport.cgi?package=%s"),
    "wiki-debian": ("Debian Wiki", "https://wiki.debian.org/FrontPage?action=fullsearch&value=%s"),
    "osm": ("OpenStreetMap", "https://www.openstreetmap.org/search?query=%s"),
}
_ENGINE_KEY_RE = re.compile(r"^SEARCH_WEB_ENGINE_([A-Z0-9]{1,20})$")


def engines(values=None):
    values = settings() if values is None else values
    out = dict(ENGINES)
    for k, v in values.items():
        m = _ENGINE_KEY_RE.match(k)
        if m and "|" in v:
            name, url = v.split("|", 1)
            if url.startswith("https://") and url.count("%s") == 1 and name.strip():
                out[m.group(1).lower()] = (name.strip()[:40], url.strip())
    return out


def web_url(engine_url, terms):
    return engine_url.replace("%s", urllib.parse.quote_plus(terms))


def web_results(query, values=None):
    """[(title, url)]: an explicit shortcut ('wiki cats', '!wiki cats') or the default engine."""
    values = settings() if values is None else values
    if not config.get_bool(values, "SEARCH_WEB", True):
        return []
    q = query.strip()
    if len(q) < 2:
        return []
    eng = engines(values)
    head, _, rest = q.partition(" ")
    key = head.lstrip("!").lower()
    if key in eng and rest.strip():
        name, url = eng[key]
        return [("Search %s for “%s”" % (name, rest.strip()), web_url(url, rest.strip()))]
    default = values.get("SEARCH_WEB_DEFAULT", "ddg").lower()
    name, url = eng.get(default, ENGINES["ddg"])
    return [("Search %s for “%s”" % (name, q), web_url(url, q))]


# --- history ----------------------------------------------------------------------------

def history_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "luxenos", "search-history.json")


def load_history():
    try:
        with open(history_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [str(x["q"]) for x in data if isinstance(x, dict) and isinstance(x.get("q"), str)]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def record_history(query, values=None):
    values = settings() if values is None else values
    q = " ".join(query.split())[:120]
    if not q or not config.get_bool(values, "SEARCH_HISTORY", True):
        return
    size = config.get_int(values, "SEARCH_HISTORY_SIZE", 20, 1, 200)
    items = [x for x in load_history() if x != q]
    items.insert(0, q)
    path = history_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([{"q": x, "t": int(time.time())} for x in items[:size]], f)
        os.replace(tmp, path)
    except OSError:
        pass


def clear_history():
    try:
        os.unlink(history_path())
    except FileNotFoundError:
        pass
