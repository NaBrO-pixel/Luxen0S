"""Hardware information for the Hardware Manager (unprivileged).

Only reliable kernel and Debian interfaces are used: /proc/cpuinfo,
/proc/meminfo, /proc/asound/cards, /sys/class/{net,power_supply,dmi},
/sys/firmware/efi, `lspci -vmm -nnk`, `lsusb`, `lsblk --json` and
`fwupdmgr get-devices --json`. A missing tool or device gives an empty
section, never an error. Parsers are pure functions (unit tested with
captured output).

Secure Boot state is read from the EFI variable (world-readable on Debian);
nothing here ever writes firmware variables or keys.
"""

import json
import os
import shutil
import subprocess

from . import sysinfo

PROC = os.environ.get("LUXENOS_HW_PROC", "/proc")
SYS = os.environ.get("LUXENOS_HW_SYS", "/sys")
EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-e0f1d3d8e23c"


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _run(argv, timeout=10):
    if not shutil.which(argv[0]):
        return None
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


# --- CPU / memory ------------------------------------------------------------------

def parse_cpuinfo(text):
    """-> {model, vendor, threads, cores, flags:set, mhz}"""
    out = {"model": "", "vendor": "", "threads": 0, "cores": 0, "flags": set(), "mhz": ""}
    pairs, phys = set(), "0"
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        if k == "processor":
            out["threads"] += 1
            phys = "0"
        elif k == "model name" and not out["model"]:
            out["model"] = " ".join(v.split())
        elif k == "vendor_id" and not out["vendor"]:
            out["vendor"] = v
        elif k == "flags" and not out["flags"]:
            out["flags"] = set(v.split())
        elif k == "cpu MHz" and not out["mhz"]:
            out["mhz"] = v.split(".")[0]
        elif k == "physical id":
            phys = v
        elif k == "core id":
            pairs.add((phys, v))
    out["cores"] = len(pairs) or out["threads"]
    return out


def virtualization(flags):
    if "vmx" in flags:
        return "Intel VT-x"
    if "svm" in flags:
        return "AMD-V"
    return ""


# --- PCI ------------------------------------------------------------------------------

def parse_lspci_vmm(text):
    """`lspci -vmm -nnk` records -> [{slot, class, vendor, device, driver, modules}]"""
    devices, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur:
                devices.append(cur)
            cur = {}
            continue
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        key = {"Slot": "slot", "Class": "class", "Vendor": "vendor", "Device": "device",
               "Driver": "driver", "Module": "modules", "Rev": "rev"}.get(k)
        if key == "modules":
            cur.setdefault("modules", []).append(v)
        elif key:
            cur[key] = v
    return devices


def strip_ids(text):
    """'Intel Corporation [8086]' -> 'Intel Corporation'"""
    t = text or ""
    if t.endswith("]") and " [" in t:
        t = t[:t.rindex(" [")]
    return t


def pci_class_is(dev, *names):
    c = strip_ids(dev.get("class", "")).lower()
    return any(n in c for n in names)


# --- USB ------------------------------------------------------------------------------

def parse_lsusb(text):
    """'Bus 001 Device 002: ID 8087:0024 Intel Corp. Hub' -> [{bus, device, id, name}]"""
    out = []
    for line in text.splitlines():
        parts = line.split(None, 6)
        if len(parts) >= 6 and parts[0] == "Bus" and parts[4] == "ID":
            out.append({"bus": parts[1], "device": parts[3].rstrip(":"), "id": parts[5],
                        "name": parts[6].strip() if len(parts) > 6 else "Unknown device"})
    return out


def is_usb_hub(dev):
    n = dev["name"].lower()
    return "root hub" in n or n.endswith(" hub") or dev["id"].startswith("1d6b:")


# --- storage --------------------------------------------------------------------------

def parse_lsblk(text):
    """lsblk --json -b -o NAME,MODEL,SIZE,TRAN,ROTA,RM,TYPE -> disks (no loop/zram)"""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    out = []
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(("loop", "zram", "ram")):
            continue
        rota = d.get("rota")
        out.append({"name": d.get("name"), "model": (d.get("model") or "").strip(),
                    "size": int(d.get("size") or 0), "transport": d.get("tran") or "",
                    "kind": "HDD" if rota in (True, "1", 1) else "SSD",
                    "removable": d.get("rm") in (True, "1", 1)})
    return out


# --- audio / network -------------------------------------------------------------------

def parse_asound_cards(text):
    """/proc/asound/cards -> [name]"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s[:1].isdigit() and "]: " in s:
            out.append(s.split("]: ", 1)[1].strip())
    return out


def network_adapters(sys_root=SYS):
    base = os.path.join(sys_root, "class", "net")
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        if n == "lo" or n.startswith(("veth", "docker", "virbr", "br-")):
            continue
        d = os.path.join(base, n)
        if not os.path.exists(os.path.join(d, "device")) and n != "waydroid0":
            continue  # virtual interface
        driver = ""
        link = os.path.join(d, "device", "driver")
        if os.path.islink(link):
            driver = os.path.basename(os.readlink(link))
        wireless = os.path.isdir(os.path.join(d, "wireless")) or os.path.exists(os.path.join(d, "phy80211"))
        out.append({"name": n, "kind": "Wi-Fi" if wireless else "Ethernet" if n.startswith(("en", "eth")) else "Other",
                    "mac": _read(os.path.join(d, "address")), "state": _read(os.path.join(d, "operstate")),
                    "driver": driver})
    return out


# --- firmware / boot ---------------------------------------------------------------------

def efi_var_byte(name, sys_root=SYS):
    """Value byte of an EFI global variable (None if unreadable/absent)."""
    path = os.path.join(sys_root, "firmware", "efi", "efivars", "%s-%s" % (name, EFI_GLOBAL))
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return data[4] if len(data) >= 5 else None


def boot_mode(sys_root=SYS):
    """{'uefi': bool, 'secure_boot': True/False/None, 'setup_mode': ...}"""
    uefi = os.path.isdir(os.path.join(sys_root, "firmware", "efi"))
    sb = setup = None
    if uefi:
        v = efi_var_byte("SecureBoot", sys_root)
        sb = None if v is None else v == 1
        v = efi_var_byte("SetupMode", sys_root)
        setup = None if v is None else v == 1
        if sb is None:
            out = _run(["mokutil", "--sb-state"])
            if out:
                sb = "enabled" in out.lower() and "disabled" not in out.lower()
    return {"uefi": uefi, "secure_boot": sb, "setup_mode": setup}


def dmi(sys_root=SYS):
    base = os.path.join(sys_root, "class", "dmi", "id")
    return {k: _read(os.path.join(base, k)) for k in
            ("sys_vendor", "product_name", "product_version", "board_name", "bios_vendor", "bios_version",
             "bios_date")}


def parse_fwupd_devices(text):
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    return [{"name": d.get("Name", ""), "version": d.get("Version", ""), "vendor": d.get("Vendor", "")}
            for d in data.get("Devices", []) if d.get("Version")]


# --- compatibility checks ------------------------------------------------------------------

def checks(cpu, mem_total, gpus, nets, boot, binder):
    """-> [(level, title, advice)] level: ok | warning | problem"""
    out = []
    virt = virtualization(cpu.get("flags", set()))
    if virt:
        out.append(("ok", "CPU virtualization (%s)" % virt, "Android apps (Waydroid) can run."))
    else:
        out.append(("warning", "No CPU virtualization flag visible",
                    "Waydroid works best with VT-x/AMD-V enabled in the firmware (BIOS/UEFI) settings."))
    if binder:
        out.append(("ok", "Android binder support in the kernel", ""))
    else:
        out.append(("warning", "Android binder support not detected",
                    "Waydroid needs the kernel's binder driver (binderfs). The Debian kernel provides it as a "
                    "module that Waydroid loads when it starts."))
    gib = mem_total / (1024 ** 3) if mem_total else 0
    if gib and gib < 3.5:
        out.append(("warning", "%.1f GiB memory" % gib, "Android apps and the desktop together want 4 GiB or more."))
    elif gib:
        out.append(("ok", "%.1f GiB memory" % gib, ""))
    for g in gpus:
        name = strip_ids(g.get("device", "graphics"))
        if not g.get("driver"):
            out.append(("problem", "No driver for %s" % name,
                        "The graphics chip has no kernel driver in use; the desktop falls back to slow software "
                        "rendering. Its firmware-* package may be missing."))
        else:
            out.append(("ok", "Graphics driver %s" % g["driver"], name))
    for n in nets:
        if not n["driver"] and n["name"] != "waydroid0":
            out.append(("problem", "No driver for network adapter %s" % n["name"],
                        "Install the matching firmware package (Software Center, search “firmware”)."))
    if not any(n["kind"] == "Wi-Fi" for n in nets):
        out.append(("warning", "No Wi-Fi adapter found",
                    "If this computer has Wi-Fi, its driver or firmware is missing (for example firmware-iwlwifi, "
                    "firmware-realtek, firmware-atheros)."))
    if boot["uefi"]:
        sb = boot["secure_boot"]
        out.append(("ok", "UEFI boot, Secure Boot %s" % {True: "on", False: "off", None: "state unknown"}[sb],
                    "See the Secure Boot page for the boot chain."))
    else:
        out.append(("ok", "Legacy BIOS boot", "Secure Boot only exists with UEFI."))
    return out


def binder_available(proc_root=PROC):
    fs = _read(os.path.join(proc_root, "filesystems"))
    if "binder" in fs or os.path.exists("/dev/binderfs") or os.path.exists("/dev/binder"):
        return True
    # The module index is world-readable; modinfo lives in /sbin.
    mods = os.path.join("/lib/modules", os.uname().release)
    for index in ("modules.builtin", "modules.dep"):
        if "/binder" in _read(os.path.join(mods, index)):
            return True
    return False


# --- collect everything ----------------------------------------------------------------------

def collect():
    """{section: [(label, value)]} plus 'checks' and 'devices'. Blocking (lspci, fwupd): thread."""
    cpu = parse_cpuinfo(_read(os.path.join(PROC, "cpuinfo")))
    mem = sysinfo.read_memory(PROC) or {}
    pci = parse_lspci_vmm(_run(["lspci", "-vmm", "-nnk"]) or "")
    gpus = [d for d in pci if pci_class_is(d, "vga", "3d controller", "display controller")]
    usb = [d for d in parse_lsusb(_run(["lsusb"]) or "") if not is_usb_hub(d)]
    disks = parse_lsblk(_run(["lsblk", "--json", "-b", "-o", "NAME,MODEL,SIZE,TRAN,ROTA,RM,TYPE"]) or "")
    nets = network_adapters()
    audio = parse_asound_cards(_read(os.path.join(PROC, "asound", "cards")))
    bats = sysinfo.read_batteries(SYS)
    boot = boot_mode()
    ids = dmi()
    fw = parse_fwupd_devices(_run(["fwupdmgr", "get-devices", "--json"], timeout=30) or "")

    def gib(n):
        return "%.1f GiB" % (n / (1024 ** 3)) if n else "unknown"

    s = {}
    s["Computer"] = [("Manufacturer", ids["sys_vendor"] or "unknown"),
                     ("Model", " ".join(filter(None, [ids["product_name"], ids["product_version"]])) or "unknown"),
                     ("Firmware", " ".join(filter(None, [ids["bios_vendor"], ids["bios_version"], ids["bios_date"]]))
                      or "unknown"),
                     ("Boot mode", "UEFI" if boot["uefi"] else "Legacy BIOS"),
                     ("Kernel", os.uname().release)]
    s["Processor"] = [("Model", cpu["model"] or "unknown"), ("Vendor", cpu["vendor"] or "unknown"),
                      ("Cores / threads", "%d / %d" % (cpu["cores"], cpu["threads"])),
                      ("Virtualization", virtualization(cpu["flags"]) or "not available")]
    if cpu["mhz"]:
        s["Processor"].append(("Current clock", cpu["mhz"] + " MHz"))
    s["Memory"] = [("Installed (usable)", gib(mem.get("total", 0))), ("Available now", gib(mem.get("available", 0))),
                   ("Swap", gib(mem.get("swap_total", 0)))]
    s["Graphics"] = [(strip_ids(g.get("device", "GPU")), "%s · driver: %s" % (strip_ids(g.get("vendor", "")),
                                                                              g.get("driver") or "none"))
                     for g in gpus]
    s["Storage"] = [(d["model"] or d["name"], "%s %s%s · /dev/%s" % (gib(d["size"]), d["kind"],
                                                                     " (removable)" if d["removable"] else "",
                                                                     d["name"]) + (" · " + d["transport"]
                                                                                   if d["transport"] else ""))
                    for d in disks]
    s["Battery"] = [(b["name"], "%s%% · %s%s" % (b["capacity"] if b["capacity"] is not None else "?", b["status"],
                                                 " · health %d%%" % b["health"] if b["health"] else ""))
                    for b in bats]
    s["Network"] = [(n["name"], "%s · %s · driver: %s · %s" % (n["kind"], n["state"], n["driver"] or "none", n["mac"]))
                    for n in nets]
    s["USB devices"] = [(d["name"], "ID %s · bus %s device %s" % (d["id"], d["bus"], d["device"])) for d in usb]
    s["Audio"] = [(a, "") for a in audio]
    s["Drivers"] = [(strip_ids(d.get("device", d.get("slot", ""))),
                     "%s · driver: %s" % (strip_ids(d.get("class", "")), d.get("driver") or "none"))
                    for d in pci]
    s["Firmware versions"] = [(f["name"], "%s %s" % (f["vendor"], f["version"]) if f["vendor"] else f["version"])
                              for f in fw]
    return {"sections": s,
            "checks": checks(cpu, mem.get("total", 0), gpus, nets, boot, binder_available()),
            "boot": boot}
