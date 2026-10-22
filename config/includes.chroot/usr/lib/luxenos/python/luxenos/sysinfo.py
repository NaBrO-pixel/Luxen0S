"""Read system metrics from the kernel's own interfaces.

/proc/stat, /proc/meminfo, /proc/net/dev, /proc/<pid>/{stat,status,cmdline},
/proc/self/mounts + statvfs and /sys/class/power_supply. Every reader takes a
root directory (for tests) and returns empty/None for anything the hardware
or kernel does not provide; nothing here raises for a missing metric.
"""

import os
import pwd
import time

PROC = "/proc"
SYS = "/sys"

PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# --- CPU ------------------------------------------------------------------------

def parse_cpu_times(stat_text):
    """'cpu  user nice system idle iowait irq softirq steal ...' -> (busy, total)."""
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            vals = [int(v) for v in line.split()[1:]]
            vals += [0] * (8 - len(vals))
            idle = vals[3] + vals[4]
            total = sum(vals[:8])
            return total - idle, total
    return 0, 0


def cpu_percent(prev, cur):
    busy = cur[0] - prev[0]
    total = cur[1] - prev[1]
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * busy / total))


def read_cpu_times(root=PROC):
    text = _read(os.path.join(root, "stat"))
    return parse_cpu_times(text) if text else (0, 0)


def cpu_count():
    return os.cpu_count() or 1


# --- memory ---------------------------------------------------------------------

def parse_meminfo(text):
    """-> dict of bytes: total, available, used, swap_total, swap_used."""
    kb = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                kb[parts[0][:-1]] = int(parts[1])
            except ValueError:
                pass
    total = kb.get("MemTotal", 0)
    avail = kb.get("MemAvailable")
    if avail is None:
        avail = kb.get("MemFree", 0) + kb.get("Buffers", 0) + kb.get("Cached", 0)
    swap_total = kb.get("SwapTotal", 0)
    swap_free = kb.get("SwapFree", 0)
    return {
        "total": total * 1024,
        "available": avail * 1024,
        "used": max(0, total - avail) * 1024,
        "swap_total": swap_total * 1024,
        "swap_used": max(0, swap_total - swap_free) * 1024,
    }


def read_memory(root=PROC):
    text = _read(os.path.join(root, "meminfo"))
    return parse_meminfo(text) if text else None


# --- network --------------------------------------------------------------------

def parse_net_dev(text):
    """-> {iface: (rx_bytes, tx_bytes)} without the loopback device."""
    out = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        name = name.strip()
        fields = data.split()
        if name == "lo" or len(fields) < 9:
            continue
        try:
            out[name] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return out


def read_net(root=PROC):
    text = _read(os.path.join(root, "net", "dev"))
    return parse_net_dev(text) if text else {}


def net_rates(prev, cur, seconds):
    """Total (rx, tx) bytes per second across interfaces present in both samples."""
    if seconds <= 0:
        return 0.0, 0.0
    rx = tx = 0
    for name, (r, t) in cur.items():
        if name in prev:
            pr, pt = prev[name]
            rx += max(0, r - pr)
            tx += max(0, t - pt)
    return rx / seconds, tx / seconds


# --- storage --------------------------------------------------------------------

REAL_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3",
           "fuseblk", "jfs", "reiserfs", "zfs", "iso9660", "udf"}


def parse_mounts(text):
    """Real, de-duplicated filesystems: [(device, mountpoint, fstype)]."""
    seen = set()
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mnt, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype not in REAL_FS or not dev.startswith("/"):
            continue
        if mnt.startswith(("/snap/", "/var/lib/waydroid", "/run/live/")):
            continue
        if dev in seen:
            continue
        seen.add(dev)
        out.append((dev, mnt, fstype))
    return out


def read_disks(root=PROC):
    text = _read(os.path.join(root, "self", "mounts")) or ""
    disks = []
    for dev, mnt, fstype in parse_mounts(text):
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        disks.append({"device": dev, "mount": mnt, "fstype": fstype, "total": total,
                      "free": free, "used": total - st.f_bfree * st.f_frsize})
    return disks


def disk_warning(disk):
    """None, 'warning' or 'critical' for low free space."""
    if disk["total"] <= 0:
        return None
    pct_free = 100.0 * disk["free"] / disk["total"]
    if disk["free"] < 1 << 30 or pct_free < 5:
        return "critical"
    if disk["free"] < 3 << 30 or pct_free < 10:
        return "warning"
    return None


# --- battery / power ------------------------------------------------------------

def read_batteries(root=SYS):
    base = os.path.join(root, "class", "power_supply")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    bats = []
    for name in names:
        d = os.path.join(base, name)
        if (_read(os.path.join(d, "type")) or "").strip() != "Battery":
            continue
        if (_read(os.path.join(d, "scope")) or "").strip() == "Device":
            continue  # mouse/keyboard batteries
        cap = (_read(os.path.join(d, "capacity")) or "").strip()
        status = (_read(os.path.join(d, "status")) or "Unknown").strip()

        def num(n):
            v = (_read(os.path.join(d, n)) or "").strip()
            return int(v) if v.lstrip("-").isdigit() else None

        power = num("power_now")
        if power is None:
            cur, volt = num("current_now"), num("voltage_now")
            power = cur * volt // 1000000 if cur is not None and volt is not None else None
        energy_now = num("energy_now") or num("charge_now")
        energy_full = num("energy_full") or num("charge_full")
        energy_design = num("energy_full_design") or num("charge_full_design")
        health = None
        if energy_full and energy_design:
            health = int(round(100.0 * energy_full / energy_design))
        bats.append({
            "name": name,
            "capacity": int(cap) if cap.isdigit() else None,
            "status": status,
            "power_w": abs(power) / 1e6 if power else None,
            "energy_now": energy_now,
            "energy_full": energy_full,
            "health": health,
        })
    return bats


def ac_online(root=SYS):
    base = os.path.join(root, "class", "power_supply")
    try:
        names = os.listdir(base)
    except OSError:
        return None
    found = None
    for name in names:
        t = (_read(os.path.join(base, name, "type")) or "").strip()
        if t in ("Mains", "USB", "USB_C", "USB_PD"):
            found = found or False
            if (_read(os.path.join(base, name, "online")) or "").strip() == "1":
                return True
    return found


def battery_summary(bats):
    """(percent, 'text') over all system batteries."""
    caps = [b["capacity"] for b in bats if b["capacity"] is not None]
    if not caps:
        return None, "Unknown"
    pct = int(round(sum(caps) / len(caps)))
    status = bats[0]["status"]
    text = "%d%% · %s" % (pct, {"Charging": "charging", "Discharging": "on battery",
                                "Full": "fully charged", "Not charging": "plugged in"}.get(status, status.lower()))
    b = bats[0]
    if b["power_w"] and b["energy_now"] and status == "Discharging":
        # energy in uWh / power in uW (or uAh / uA): hours left.
        hours = b["energy_now"] / (b["power_w"] * 1e6)
        if 0 < hours < 48:
            text += ", %d:%02d left" % (int(hours), int((hours % 1) * 60))
    return pct, text


# --- processes ------------------------------------------------------------------

_users = {}


def username(uid):
    if uid not in _users:
        try:
            _users[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _users[uid] = str(uid)
    return _users[uid]


def parse_pid_stat(text):
    """-> (comm, state, ppid, utime+stime ticks, starttime ticks, rss pages)."""
    lpar = text.index("(")
    rpar = text.rindex(")")
    comm = text[lpar + 1:rpar]
    rest = text[rpar + 2:].split()
    state = rest[0]
    ppid = int(rest[1])
    ticks = int(rest[11]) + int(rest[12])
    start = int(rest[19])
    rss = int(rest[21])
    return comm, state, ppid, ticks, start, rss


def read_processes(root=PROC):
    """{pid: dict} for every readable process (kernel threads included, flagged)."""
    procs = {}
    try:
        names = os.listdir(root)
    except OSError:
        return procs
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        base = os.path.join(root, name)
        stat = _read(os.path.join(base, "stat"))
        if not stat:
            continue
        try:
            comm, state, ppid, ticks, start, rss = parse_pid_stat(stat)
        except (ValueError, IndexError):
            continue
        try:
            uid = os.stat(base).st_uid
        except OSError:
            continue
        cmdline = (_read(os.path.join(base, "cmdline")) or "").replace("\0", " ").strip()
        procs[pid] = {
            "pid": pid,
            "name": comm,
            "state": state,
            "ppid": ppid,
            "ticks": ticks,
            "start": start,
            "rss": rss * PAGE,
            "uid": uid,
            "user": username(uid),
            "cmdline": cmdline,
            "kernel": not cmdline and ppid in (0, 2),
        }
    return procs


def process_cpu(prev, cur, seconds, ncpu=None):
    """Annotate cur processes with 'cpu' percent of ONE core (top-style)."""
    hz = CLK_TCK
    for pid, p in cur.items():
        old = prev.get(pid)
        if old is None or old["start"] != p["start"] or seconds <= 0:
            p["cpu"] = 0.0
        else:
            p["cpu"] = max(0.0, 100.0 * (p["ticks"] - old["ticks"]) / hz / seconds)
    return cur


class Sampler:
    """Keeps the previous sample to turn counters into rates."""

    def __init__(self, proc_root=PROC):
        self.root = proc_root
        self.t = time.monotonic()
        self.cpu = read_cpu_times(proc_root)
        self.net = read_net(proc_root)
        self.procs = {}

    def sample(self, with_processes=False):
        now = time.monotonic()
        dt = now - self.t
        cpu = read_cpu_times(self.root)
        net = read_net(self.root)
        result = {
            "cpu": cpu_percent(self.cpu, cpu),
            "memory": read_memory(self.root),
            "net": net_rates(self.net, net, dt),
            "seconds": dt,
        }
        if with_processes:
            procs = read_processes(self.root)
            process_cpu(self.procs, procs, dt)
            self.procs = procs
            result["processes"] = procs
        self.t, self.cpu, self.net = now, cpu, net
        return result
