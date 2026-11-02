"""Waydroid (Android) logic for the Android Integration Center.

Everything an ordinary user may do runs as the user through the `waydroid`
command: session start/stop, installing APKs, listing/launching/removing
apps, display properties. Only the container service and the image
download go through /usr/libexec/luxenos/luxenos-android-root (pkexec).
The first-boot service created by config/hooks/live/0100-install-waydroid
is reused, never replaced.

Parsers and the troubleshooting checks are pure (unit tested).
"""

import os
import re
import shutil
import subprocess

from . import config, hardware

ROOT_HELPER = "/usr/libexec/luxenos/luxenos-android-root"
BASE_PROP = "/var/lib/waydroid/waydroid_base.prop"
IMAGES_DIR = "/var/lib/waydroid/images"
APK_BUNDLED = "/usr/share/luxenos/apks/AuroraStore.apk"
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
RESOLUTIONS = (("Automatic (follow the window)", None, None), ("1280 × 720", 1280, 720),
               ("1600 × 900", 1600, 900), ("1920 × 1080", 1920, 1080), ("720 × 1280 (portrait)", 720, 1280),
               ("1080 × 1920 (portrait)", 1080, 1920))

# (pattern in Waydroid output, explanation)
ERRORS = (
    ("not initialized", "Android has not been set up yet. Use “Set up Android” (downloads about 1 GB)."),
    ("Failed to get service waydroidplatform", "The Android session is not running. Start Android first."),
    ("session is not running", "The Android session is not running. Start Android first."),
    ("WAYLAND_DISPLAY is not set", "Waydroid must be started from inside the LuxenOS desktop session."),
    ("binder", "The kernel's Android binder driver is not available. Restart the computer; if it persists, "
               "check `lsmod | grep binder` and the Hardware Manager's compatibility page."),
    ("Container is not running", "The Android container service is stopped. Use “Restart Android services”."),
    ("lxc", "The Android container (LXC) could not start. Use “Restart Android services”, then look at the log."),
    ("Failed to download", "The Android image could not be downloaded. Check the internet connection and retry."),
    ("No space left", "The disk is full. Android needs about 3 GB of free space."),
    ("INSTALL_FAILED_NO_MATCHING_ABIS", "This APK was built for a different processor (for example ARM only); "
                                        "it cannot run on this computer."),
    ("INSTALL_FAILED_INSUFFICIENT_STORAGE", "Android has no space left for this app."),
    ("INSTALL_FAILED_VERSION_DOWNGRADE", "A newer version of this app is already installed."),
    ("INSTALL_PARSE_FAILED", "This file is not a valid Android app (APK)."),
)


def available():
    return shutil.which("waydroid") is not None


def initialized(base_prop=BASE_PROP):
    try:
        return os.path.getsize(base_prop) > 0
    except OSError:
        return False


def parse_status(text):
    """`waydroid status` -> {'session': 'RUNNING'|'STOPPED', 'container': ..., 'initialized': bool, ...}"""
    out = {"initialized": "not initialized" not in text.lower()}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        key = {"Session": "session", "Container": "container", "Vendor type": "vendor",
               "IP address": "ip", "Session user": "user", "Wayland display": "display"}.get(k)
        if key:
            out[key] = v
    return out


def parse_app_list(text):
    """`waydroid app list` -> [{'name', 'package'}] (sorted by name)."""
    apps, cur = [], {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Name:"):
            if cur.get("package"):
                apps.append(cur)
            cur = {"name": s[5:].strip(), "package": ""}
        elif s.startswith("packageName:"):
            cur["package"] = s[12:].strip()
    if cur.get("package"):
        apps.append(cur)
    apps = [a for a in apps if PKG_RE.match(a["package"])]
    return sorted(apps, key=lambda a: a["name"].lower())


def explain(output):
    low = output.lower()
    for pattern, text in ERRORS:
        if pattern.lower() in low:
            return text
    return None


def run(args, timeout=60):
    """waydroid ARGS as the user -> (rc, combined output)."""
    if not available():
        return 127, "Waydroid is not installed"
    try:
        p = subprocess.run(["waydroid"] + list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "waydroid %s did not finish in %d seconds" % (args[0], timeout)
    except OSError as e:
        return 126, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def status():
    rc, out = run(["status"], timeout=15)
    st = parse_status(out) if rc == 0 else {"initialized": initialized(), "error": out}
    st.setdefault("session", "STOPPED")
    st.setdefault("container", "UNKNOWN")
    return st


def start_session():
    """`waydroid session start` stays in the foreground: detach it."""
    subprocess.Popen(["waydroid", "session", "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def valid_apk(path):
    if not path.lower().endswith(".apk") or not os.path.isfile(path):
        return "not an .apk file"
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"PK\x03\x04":
                return "the file is not an Android package (APK files are ZIP archives)"
    except OSError as e:
        return e.strerror
    return None


def set_resolution(width, height):
    """Display size properties; None/None = automatic. Takes effect after a restart of the session."""
    props = (("persist.waydroid.width", "" if width is None else str(int(width))),
             ("persist.waydroid.height", "" if height is None else str(int(height))))
    for key, value in props:
        rc, out = run(["prop", "set", key, value], timeout=20)
        if rc != 0:
            return out or "waydroid prop set failed"
    return None


def storage(home=None):
    """{'images': bytes, 'data': bytes or None (not readable), 'free': bytes}"""
    home = home or os.path.expanduser("~")
    images = 0
    try:
        for n in os.listdir(IMAGES_DIR):
            images += os.path.getsize(os.path.join(IMAGES_DIR, n))
    except OSError:
        pass
    data_dir = os.path.join(home, ".local", "share", "waydroid", "data")
    data = None
    if os.path.isdir(data_dir):
        try:
            out = subprocess.run(["du", "-sb", data_dir], capture_output=True, text=True, timeout=30,
                                 check=False).stdout.split()
            data = int(out[0]) if out and out[0].isdigit() else None
        except (OSError, subprocess.SubprocessError):
            data = None
    try:
        st = os.statvfs(home)
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = 0
    return {"images": images, "data": data, "free": free, "data_dir": data_dir}


def service_state(unit):
    if not shutil.which("systemctl"):
        return "unknown"
    try:
        return subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def checks(st, first_boot, container_state, binder, wayland, mem_total, free):
    """[(level, title, advice)] for the troubleshooting page."""
    out = []
    if not st.get("initialized"):
        if first_boot == "activating":
            out.append(("warning", "Android is being set up right now", "The first-boot service is downloading "
                        "the Android image; this takes several minutes."))
        elif first_boot == "failed":
            out.append(("problem", "The first-boot Android setup failed",
                        "Usually the network was not ready. Use “Retry first-boot setup”."))
        else:
            out.append(("problem", "Android is not set up", "Use “Set up Android”."))
    else:
        out.append(("ok", "Android is set up", st.get("vendor", "")))
    if container_state != "active":
        out.append(("problem" if st.get("initialized") else "warning", "Android container service: %s" % container_state,
                    "Use “Restart Android services”."))
    else:
        out.append(("ok", "Android container service is running", ""))
    out.append(("ok", "Binder driver available", "") if binder else
               ("problem", "Binder driver not found", "Waydroid needs the kernel's binder driver (binder_linux)."))
    if not wayland:
        out.append(("problem", "Not in a Wayland session", "Start Android from the LuxenOS desktop."))
    if mem_total and mem_total < 3.5 * 1024 ** 3:
        out.append(("warning", "Little memory (%.1f GiB)" % (mem_total / 1024 ** 3),
                    "Close other programs while using Android apps."))
    if free and free < 3 * 1024 ** 3:
        out.append(("warning", "Less than 3 GB free on the disk", "Android and its apps need room to work."))
    return out


def collect_checks():
    from . import sysinfo
    st = status()
    mem = (sysinfo.read_memory() or {}).get("total", 0)
    return checks(st, service_state("waydroid-first-init.service"), service_state("waydroid-container.service"),
                  hardware.binder_available(), bool(os.environ.get("WAYLAND_DISPLAY")), mem,
                  storage()["free"])


def shortcut_path(package):
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "applications", "luxenos-android-%s.desktop" % package)


def waydroid_entry_exists(package):
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.exists(os.path.join(base, "applications", "waydroid.%s.desktop" % package))


def shortcut_text(name, package):
    if not PKG_RE.match(package):
        raise ValueError("invalid package name")
    clean = " ".join(name.replace("\n", " ").split())[:80] or package
    return ("[Desktop Entry]\nType=Application\nName=%s\nComment=Android app (Waydroid)\n"
            "Exec=waydroid app launch %s\nIcon=android\nCategories=Utility;X-WayDroid-App;\nTerminal=false\n"
            "X-LuxenOS-Android=%s\n" % (clean, package, package))


def create_shortcut(name, package):
    path = shortcut_path(package)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(shortcut_text(name, package))
    os.replace(path + ".tmp", path)
    return path


def first_run_settings():
    return config.read_kv(os.path.join(config.luxenos_config_dir(), "android.conf"))
