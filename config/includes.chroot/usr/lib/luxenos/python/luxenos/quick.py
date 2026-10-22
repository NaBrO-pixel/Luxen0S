"""System-state backends for Quick Settings.

Each control reads the real state from the service that owns it and changes
it through that service's normal client tool, called with an argv list (no
shell). A control whose tool or hardware is missing reports itself as
unavailable instead of pretending:

  Wi-Fi           NetworkManager (nmcli; polkit allows the active session)
  Bluetooth       BlueZ (bluetoothctl)
  Volume          PipeWire/WirePlumber (wpctl)
  Brightness      brightnessctl (backlight class devices)
  Night light     wlsunset (a per-user process; remembered across logins)
  Do Not Disturb  dunst (dunstctl)
  Battery         /sys/class/power_supply
  Power profile   luxenos-power-profile-status / -cycle (TLP, existing)

Parsers are pure functions so they are unit tested with captured output.
"""

import json
import os
import re
import shutil
import signal
import subprocess

from . import config, sysinfo

TIMEOUT = 4


class Unavailable(Exception):
    pass


def run(argv, timeout=TIMEOUT):
    """Run argv, return stdout. Raises Unavailable / RuntimeError."""
    if not shutil.which(argv[0]):
        raise Unavailable("%s is not installed" % argv[0])
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("%s did not answer in time" % argv[0])
    except OSError as e:
        raise Unavailable(str(e))
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip().splitlines()
        raise RuntimeError(msg[-1] if msg else "%s failed (exit %d)" % (argv[0], p.returncode))
    return p.stdout


# --- parsers ------------------------------------------------------------------

def parse_wpctl_volume(text):
    """'Volume: 0.40 [MUTED]' -> (40, True)"""
    m = re.search(r"Volume:\s*([0-9.]+)", text)
    if not m:
        raise ValueError("unexpected wpctl output: %r" % text)
    return int(round(float(m.group(1)) * 100)), "[MUTED]" in text


def parse_brightnessctl(text):
    """machine output 'intel_backlight,backlight,500,50%,1000' -> 50"""
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 5 and parts[1] == "backlight":
            try:
                cur, mx = int(parts[2]), int(parts[4])
            except ValueError:
                continue
            return int(round(cur * 100 / mx)) if mx > 0 else 0
    raise ValueError("no backlight device")


def parse_nmcli_general(text):
    """'nmcli -t -f WIFI-HW,WIFI general' -> (hw_enabled, enabled)"""
    line = text.strip().splitlines()[0] if text.strip() else ""
    parts = line.split(":")
    if len(parts) < 2:
        raise ValueError("unexpected nmcli output: %r" % text)
    return parts[0] == "enabled", parts[1] == "enabled"


def nmcli_split(line):
    """Split an nmcli terse line on unescaped ':'."""
    fields, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    fields.append(cur)
    return fields


def parse_nmcli_devices(text):
    """'nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device' -> [dict]"""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = nmcli_split(line)
        if len(f) >= 4:
            out.append({"device": f[0], "type": f[1], "state": f[2], "connection": f[3]})
    return out


def parse_bluetoothctl_show(text):
    """-> (controller_present, powered)"""
    if "Controller" not in text:
        return False, False
    m = re.search(r"^\s*Powered:\s*(yes|no)", text, re.M)
    return True, bool(m and m.group(1) == "yes")


def parse_power_profile(text):
    try:
        data = json.loads(text)
    except ValueError:
        return "balanced"
    tip = data.get("tooltip", "")
    m = re.search(r"profile:\s*(\w+)", tip)
    return m.group(1) if m else "balanced"


# --- controls -------------------------------------------------------------------

class WiFi:
    label = "Wi-Fi"

    def available(self):
        if not shutil.which("nmcli"):
            return False, "NetworkManager is not installed"
        try:
            devs = parse_nmcli_devices(run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]))
        except (Unavailable, RuntimeError) as e:
            return False, "NetworkManager is not running (%s)" % e
        if not any(d["type"] == "wifi" for d in devs):
            return False, "No Wi-Fi adapter"
        return True, ""

    def state(self):
        hw, on = parse_nmcli_general(run(["nmcli", "-t", "-f", "WIFI-HW,WIFI", "general"]))
        devs = parse_nmcli_devices(run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]))
        conn = next((d["connection"] for d in devs if d["type"] == "wifi" and d["state"] == "connected"), "")
        if not hw:
            return False, "Blocked by hardware switch"
        return on, (conn or ("Not connected" if on else "Off"))

    def set(self, on):
        run(["nmcli", "radio", "wifi", "on" if on else "off"])


class Bluetooth:
    label = "Bluetooth"

    def available(self):
        if not shutil.which("bluetoothctl"):
            return False, "BlueZ is not installed"
        if not os.path.isdir("/sys/class/bluetooth") or not os.listdir("/sys/class/bluetooth"):
            return False, "No Bluetooth adapter"
        return True, ""

    def state(self):
        present, powered = parse_bluetoothctl_show(run(["bluetoothctl", "show"]))
        if not present:
            return False, "Adapter not ready (blocked?)"
        return powered, "On" if powered else "Off"

    def set(self, on):
        out = run(["bluetoothctl", "power", "on" if on else "off"])
        if "Failed" in out:
            hint = " Is it blocked? Check `rfkill list`." if on else ""
            raise RuntimeError("Bluetooth could not be switched %s.%s" % ("on" if on else "off", hint))


class Volume:
    label = "Volume"
    SINK = "@DEFAULT_AUDIO_SINK@"

    def available(self):
        if not shutil.which("wpctl"):
            return False, "PipeWire tools (wpctl) are not installed"
        try:
            self.state()
        except (Unavailable, RuntimeError, ValueError):
            return False, "No audio output"
        return True, ""

    def state(self):
        return parse_wpctl_volume(run(["wpctl", "get-volume", self.SINK]))

    def set_level(self, percent):
        percent = max(0, min(100, int(percent)))
        run(["wpctl", "set-volume", self.SINK, "%d%%" % percent])

    def set_mute(self, mute):
        run(["wpctl", "set-mute", self.SINK, "1" if mute else "0"])


class Brightness:
    label = "Brightness"

    def available(self):
        if not shutil.which("brightnessctl"):
            return False, "brightnessctl is not installed"
        if not os.path.isdir("/sys/class/backlight") or not os.listdir("/sys/class/backlight"):
            return False, "No adjustable backlight (external monitor?)"
        return True, ""

    def state(self):
        return parse_brightnessctl(run(["brightnessctl", "-m", "-c", "backlight", "info"]))

    def set_level(self, percent):
        # Never let the slider switch the panel completely dark.
        percent = max(5, min(100, int(percent)))
        run(["brightnessctl", "-q", "-c", "backlight", "set", "%d%%" % percent])


QS_CONF = "quick-settings.conf"


def qs_conf_path():
    return os.path.join(config.luxenos_config_dir(), QS_CONF)


class NightLight:
    """wlsunset with day and night temperature 1 K apart = always warm.

    The on/off choice is stored in ~/.config/luxenos/quick-settings.conf and
    luxenos-session-init restores it at login.
    """
    label = "Night light"
    TEMP = 4000

    def available(self):
        if not shutil.which("wlsunset"):
            return False, "wlsunset is not installed"
        return True, ""

    def _pids(self):
        try:
            out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "wlsunset"],
                                 capture_output=True, text=True, timeout=TIMEOUT, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [int(p) for p in out.split() if p.isdigit()]

    def state(self):
        on = bool(self._pids())
        return on, ("Warm (%d K)" % self.TEMP) if on else "Off"

    def set(self, on):
        for pid in self._pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if on:
            subprocess.Popen(["wlsunset", "-t", str(self.TEMP), "-T", str(self.TEMP + 1)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        config.update_kv(qs_conf_path(), {"NIGHT_LIGHT": "yes" if on else "no"},
                         header="# LuxenOS Quick Settings state (restored at login).")


class DoNotDisturb:
    label = "Do Not Disturb"

    def available(self):
        if not shutil.which("dunstctl"):
            return False, "dunst is not installed"
        try:
            self.state()
        except (Unavailable, RuntimeError):
            return False, "Notification daemon is not running"
        return True, ""

    def state(self):
        paused = run(["dunstctl", "is-paused"]).strip() == "true"
        return paused, "Notifications paused" if paused else "Off"

    def set(self, on):
        run(["dunstctl", "set-paused", "true" if on else "false"])


class Battery:
    label = "Battery"

    def available(self):
        if not sysinfo.read_batteries():
            return False, "No battery"
        return True, ""

    def state(self):
        bats = sysinfo.read_batteries()
        if not bats:
            return None, "No battery"
        return sysinfo.battery_summary(bats)


class PowerProfile:
    label = "Power profile"
    NAMES = {"balanced": "Balanced", "powersave": "Power saver", "performance": "Performance"}

    def available(self):
        if not shutil.which("luxenos-power-profile-status"):
            return False, "Power profiles are not installed"
        if not shutil.which("tlp"):
            return False, "TLP is not installed"
        return True, ""

    def state(self):
        mode = parse_power_profile(run(["luxenos-power-profile-status"]))
        return mode, self.NAMES.get(mode, mode)

    def cycle(self):
        run(["luxenos-power-profile-cycle"], timeout=30)
