"""Package logic for the Software Center (python3-apt for reading).

Reading the catalogue needs no privileges: python3-apt opens the same cache
apt uses. Every change goes through /usr/libexec/luxenos/luxenos-pkg-root
via pkexec (polkit action org.luxenos.pkg.install, administrator password
each time), which calls apt-get with an argv list. That keeps Debian's own
protections in place: repository signatures are verified by apt (no
--allow-unauthenticated anywhere), the dpkg lock serialises package managers,
and the helper's own lock serialises LuxenOS's tools.

Pure helpers here are unit tested.
"""

import os
import re
import subprocess

ROOT_HELPER = "/usr/libexec/luxenos/luxenos-pkg-root"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]+(:[a-z0-9]+)?$")

# Debian section -> Software Center category.
CATEGORIES = (
    ("internet", "Internet", "web-browser-symbolic", ("web", "net", "mail", "news", "comm", "httpd")),
    ("office", "Office and Text", "x-office-document-symbolic", ("text", "editors", "tex")),
    ("graphics", "Graphics and Photos", "applications-graphics-symbolic", ("graphics",)),
    ("media", "Audio and Video", "applications-multimedia-symbolic", ("sound", "video")),
    ("games", "Games", "applications-games-symbolic", ("games",)),
    ("science", "Science and Education", "applications-science-symbolic",
     ("science", "math", "education", "electronics", "hamradio", "gnu-r")),
    ("development", "Development", "applications-engineering-symbolic",
     ("devel", "vcs", "python", "perl", "ruby", "java", "javascript", "rust", "golang", "lisp", "ocaml",
      "haskell", "php", "interpreters", "database")),
    ("system", "System and Utilities", "applications-utilities-symbolic",
     ("utils", "admin", "misc", "shells", "x11", "gnome", "kde", "xfce", "otherosfs", "embedded")),
    ("fonts", "Fonts", "font-x-generic-symbolic", ("fonts",)),
)

# Shown on the Explore page and offered by the welcome guide. Only names: the
# catalogue decides whether they exist and where they come from.
SUGGESTED = (
    ("libreoffice", "Office suite: documents, spreadsheets, presentations"),
    ("gimp", "Photo editing and image manipulation"),
    ("vlc", "Plays almost any audio and video file"),
    ("thunderbird", "Email, calendar and contacts"),
    ("inkscape", "Vector graphics editor"),
    ("gnome-calculator", "Calculator"),
    ("keepassxc", "Password manager"),
    ("transmission-gtk", "BitTorrent client"),
    ("audacity", "Audio recording and editing"),
    ("gnome-disk-utility", "Manage disks and USB drives"),
    ("blueman", "Bluetooth device manager"),
    ("simple-scan", "Scan documents"),
)

# Removing these would break the desktop or the system; the helper refuses
# any operation that would remove one, the GUI warns before that.
PROTECTED = ("sway", "swaybg", "swaylock", "swayidle", "waybar", "greetd", "wlgreet", "network-manager",
             "polkitd", "pkexec", "sudo", "systemd", "dbus", "apt", "dpkg", "python3", "python3-gi",
             "python3-apt", "zenity", "linux-image-amd64", "grub-pc", "grub-efi-amd64-bin", "foot")

_LIB_RE = re.compile(r"^(lib.*|.*-(dev|dbg|dbgsym|doc|data|common|l10n-.*|locale.*|perl|java)|"
                     r"python3?-.*|golang-.*|librust-.*|node-.*|r-cran-.*|r-bioc-.*|ruby-.*|php-.*|"
                     r"haskell-.*|ocaml-.*|elpa-.*|texlive-.*|fonts-.*|firmware-.*|linux-(image|headers|modules).*)$")


def valid_name(name):
    return bool(NAME_RE.match(name)) and not name.endswith("-") and len(name) <= 128


def section_base(section):
    return (section or "").split("/")[-1]


def category_of(section):
    base = section_base(section)
    for key, _title, _icon, sections in CATEGORIES:
        if base in sections:
            return key
    return "system"


def is_library_like(name, section=""):
    """Packages that are rarely installed on purpose (libraries, -dev, ...)."""
    if section_base(section) in ("libs", "libdevel", "oldlibs", "debug", "doc", "localization", "kernel"):
        return True
    return bool(_LIB_RE.match(name))


def classify_origins(origins):
    """origins: [{'origin','label','site','archive','trusted'}] of a version.

    -> ('debian'|'thirdparty'|'local'|'unverified', human description)
    """
    if not origins:
        return "local", "Local package (no repository)"
    remote = [o for o in origins if o.get("archive") != "now" and o.get("site")]
    if not remote:
        return "local", "Installed from a local file, not from any repository"
    for o in remote:
        if o.get("origin") == "Debian" and o.get("label", "").startswith("Debian") and o.get("trusted"):
            return "debian", "Official Debian package (%s)" % (o.get("archive") or o.get("site"))
    o = remote[0]
    who = o.get("origin") or o.get("label") or o.get("site")
    if not any(x.get("trusted") for x in remote):
        return "unverified", "Unverified source %s: apt will refuse to install it" % who
    return "thirdparty", "Third-party repository: %s (%s)" % (who, o.get("site"))


BADGES = {
    "debian": ("Debian", "debian"),
    "thirdparty": ("Third-party", "thirdparty"),
    "local": ("Local", "local"),
    "unverified": ("Unverified", "local"),
}


def parse_status_line(line):
    """apt Status-Fd / helper protocol -> (kind, percent or None, text)."""
    line = line.rstrip("\n")
    if line.startswith("@@STATUS "):
        return "status", None, line[9:]
    if line.startswith("@@LOG "):
        return "log", None, line[6:]
    for prefix in ("pmstatus:", "dlstatus:"):
        if line.startswith(prefix):
            parts = line.split(":", 3)
            if len(parts) == 4:
                try:
                    pct = float(parts[2])
                except ValueError:
                    pct = None
                kind = "install" if prefix == "pmstatus:" else "download"
                return kind, pct, parts[3]
    if line.startswith(("pmerror:", "pmconffile:")):
        return "log", None, line.split(":", 3)[-1]
    return "other", None, line


def parse_simulation(text):
    """apt-get -s output -> {'install': [...], 'upgrade': [...], 'remove': [...], 'errors': [...]}"""
    out = {"install": [], "upgrade": [], "remove": [], "errors": []}
    for line in text.splitlines():
        if line.startswith("Inst "):
            name = line.split()[1]
            (out["upgrade"] if "[" in line.split(" (")[0] else out["install"]).append(name)
        elif line.startswith("Remv "):
            out["remove"].append(line.split()[1])
        elif line.startswith("E: "):
            out["errors"].append(line[3:])
    return out


def simulate(action, names):
    """Ask apt what an operation would do, unprivileged (apt-get -s)."""
    if action not in ("install", "remove", "upgrade"):
        raise ValueError("unknown action")
    for n in names:
        if not valid_name(n):
            raise ValueError("invalid package name: %r" % n)
    argv = ["apt-get", "-s", "-q"]
    argv += ["--with-new-pkgs", "upgrade"] if action == "upgrade" else [action, "--"] + list(names)
    env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
    p = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env, check=False)
    result = parse_simulation(p.stdout + "\n" + p.stderr)
    if p.returncode != 0 and not result["errors"]:
        tail = (p.stderr or p.stdout).strip().splitlines()
        result["errors"].append(tail[-1] if tail else "apt-get failed")
    return result


def diagnose(log_lines):
    """Turn apt/dpkg failure output into advice. -> (summary, repair_needed)"""
    text = "\n".join(log_lines)
    if "dpkg was interrupted" in text or "dpkg --configure -a" in text:
        return ("A previous package operation was interrupted. Use “Repair” to finish it "
                "(runs dpkg --configure -a and apt-get -f install).", True)
    if "Could not get lock" in text or "another package operation" in text or "Unable to acquire" in text:
        return ("Another program is installing or updating software (for example automatic "
                "updates). Wait for it to finish, then try again.", False)
    if "Temporary failure resolving" in text or "Could not resolve" in text or "Failed to fetch" in text \
            or "Network is unreachable" in text:
        return ("The package servers could not be reached. Check the network connection and try "
                "“Refresh catalogue” again.", False)
    if "NO_PUBKEY" in text or "not signed" in text or "is not signed" in text or "EXPKEYSIG" in text:
        return ("A repository's signature could not be verified, so apt refused to use it. This is a "
                "safety check: fix or remove that repository instead of bypassing it.", False)
    if "Unable to locate package" in text or "has no installation candidate" in text:
        return ("The package is not in the downloaded catalogue. Use “Refresh catalogue” and try again.", False)
    if "unmet dependencies" in text or "held broken packages" in text:
        return ("The package's dependencies cannot be satisfied with the enabled repositories.", True)
    if "No space left on device" in text:
        return ("The disk is full. Free some space and try again.", False)
    if "protected package" in text:
        return ("This would remove a part of LuxenOS the desktop needs, so it was refused.", False)
    return ("The package manager reported an error; see the details below.", False)


def lists_present(lists_dir="/var/lib/apt/lists"):
    try:
        return any(n.endswith("_Packages") or n.endswith("_Packages.lz4") or "_Packages" in n
                   for n in os.listdir(lists_dir))
    except OSError:
        return False


def dpkg_interrupted(updates_dir="/var/lib/dpkg/updates"):
    try:
        return any(n.isdigit() for n in os.listdir(updates_dir))
    except OSError:
        return False


# --- catalogue (python3-apt) --------------------------------------------------

class PackageInfo:
    __slots__ = ("name", "summary", "description", "section", "installed_version", "candidate_version",
                 "upgradable", "auto", "origin_kind", "origin_text", "size", "homepage", "category",
                 "library")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def installed(self):
        return self.installed_version is not None


def _origins(ver):
    out = []
    for o in getattr(ver, "origins", []) or []:
        out.append({"origin": o.origin, "label": o.label, "site": o.site,
                    "archive": o.archive, "trusted": bool(o.trusted)})
    return out


def load_catalogue():
    """[PackageInfo] for every package with a version (blocking: run in a thread)."""
    import apt
    cache = apt.Cache()
    result = []
    for p in cache:
        inst = p.installed
        cand = p.candidate
        ver = cand or inst
        if ver is None:
            continue
        kind, text = classify_origins(_origins(cand) if cand is not None else _origins(inst))
        section = ver.section or ""
        result.append(PackageInfo(
            name=p.shortname if p.shortname else p.name,
            summary=ver.summary or "",
            description=None,
            section=section,
            installed_version=inst.version if inst is not None else None,
            candidate_version=cand.version if cand is not None else None,
            upgradable=bool(p.is_upgradable),
            auto=bool(p.is_auto_installed) if inst is not None else False,
            origin_kind=kind,
            origin_text=text,
            size=ver.installed_size or 0,
            homepage=ver.homepage or "",
            category=category_of(section),
            library=is_library_like(p.shortname or p.name, section),
        ))
    result.sort(key=lambda i: i.name)
    return result


def describe(name):
    """Long description of one package (python3-apt)."""
    import apt
    cache = apt.Cache()
    if name not in cache:
        return ""
    p = cache[name]
    ver = p.candidate or p.installed
    return (ver.description or "") if ver is not None else ""


def search(catalogue, query, category=None, include_libraries=False, limit=300):
    q = query.strip().lower()
    names, summaries = [], []
    for p in catalogue:
        if category and p.category != category:
            continue
        if not include_libraries and p.library and not (q and p.name == q):
            continue
        if not q:
            names.append(p)
        elif q in p.name:
            names.append(p)
        elif q in p.summary.lower():
            summaries.append(p)
    if q:
        names.sort(key=lambda p: (p.name != q, not p.name.startswith(q), len(p.name), p.name))
    return (names + summaries)[:limit]
