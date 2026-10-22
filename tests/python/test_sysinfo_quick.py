import os
import tempfile
import unittest

import helpers
from luxenos import monitor, quick, sysinfo


assert helpers.CHROOT  # imported for its sys.path setup


class SysinfoTest(unittest.TestCase):
    def test_cpu(self):
        a = sysinfo.parse_cpu_times("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4")
        b = sysinfo.parse_cpu_times("cpu  150 0 150 900 0 0 0 0 0 0")
        self.assertEqual(a, (200, 1000))
        self.assertAlmostEqual(sysinfo.cpu_percent(a, b), 50.0)
        self.assertEqual(sysinfo.cpu_percent(a, a), 0.0)

    def test_meminfo(self):
        m = sysinfo.parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n"
                                  "SwapTotal: 0 kB\nSwapFree: 0 kB\n")
        self.assertEqual(m["used"], 600 * 1024)
        self.assertEqual(m["swap_total"], 0)
        old = sysinfo.parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 50 kB\n")
        self.assertEqual(old["available"], 200 * 1024)

    def test_net(self):
        text = ("Inter-|Receive\n face |bytes\n"
                "    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n"
                "  eth0: 1000 5 0 0 0 0 0 0 2000 5 0 0 0 0 0 0\n")
        cur = sysinfo.parse_net_dev(text)
        self.assertEqual(cur, {"eth0": (1000, 2000)})
        self.assertEqual(sysinfo.net_rates({"eth0": (0, 0)}, cur, 2.0), (500.0, 1000.0))

    def test_mounts_filter(self):
        text = ("/dev/sda2 / ext4 rw 0 0\nproc /proc proc rw 0 0\n/dev/sda2 /home ext4 rw 0 0\n"
                "/dev/sdb1 /media/u/My\\040Disk vfat rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n")
        self.assertEqual(sysinfo.parse_mounts(text),
                         [("/dev/sda2", "/", "ext4"), ("/dev/sdb1", "/media/u/My Disk", "vfat")])

    def test_disk_warning(self):
        g = 1 << 30
        self.assertEqual(sysinfo.disk_warning({"total": 100 * g, "free": 50 * g}), None)
        self.assertEqual(sysinfo.disk_warning({"total": 100 * g, "free": 8 * g}), "warning")
        self.assertEqual(sysinfo.disk_warning({"total": 100 * g, "free": g // 2}), "critical")

    def test_pid_stat_with_spaces_in_name(self):
        text = "42 (my (odd) prog) S 1 42 42 0 -1 4194560 100 0 0 0 7 3 0 0 20 0 1 0 555 1000 25 0 0"
        self.assertEqual(sysinfo.parse_pid_stat(text), ("my (odd) prog", "S", 1, 10, 555, 25))

    def test_fake_proc_and_sys(self):
        with tempfile.TemporaryDirectory() as root:
            p = os.path.join(root, "123")
            os.makedirs(p)
            with open(os.path.join(p, "stat"), "w") as f:
                f.write("123 (bash) S 1 1 1 0 -1 0 0 0 0 0 5 5 0 0 20 0 1 0 9 0 10 0")
            with open(os.path.join(p, "cmdline"), "w") as f:
                f.write("bash\0-l\0")
            procs = sysinfo.read_processes(root)
            self.assertEqual(procs[123]["cmdline"], "bash -l")
            self.assertFalse(procs[123]["kernel"])
            bat = os.path.join(root, "class", "power_supply", "BAT0")
            os.makedirs(bat)
            for k, v in (("type", "Battery"), ("capacity", "8"), ("status", "Discharging"),
                         ("energy_now", "5000000"), ("power_now", "10000000"),
                         ("energy_full", "40000000"), ("energy_full_design", "50000000")):
                with open(os.path.join(bat, k), "w") as f:
                    f.write(v + "\n")
            bats = sysinfo.read_batteries(root)
            self.assertEqual(bats[0]["health"], 80)
            pct, text = sysinfo.battery_summary(bats)
            self.assertEqual(pct, 8)
            self.assertIn("0:30 left", text)
            warns = monitor.warnings_for({"batteries": bats})
            self.assertTrue(any("Battery is low" in t for _, t in warns))
        self.assertEqual(sysinfo.read_batteries("/nonexistent"), [])

    def test_monitor_warnings(self):
        mem = {"total": 100, "used": 96, "swap_total": 0, "swap_used": 0}
        w = monitor.warnings_for({"cpu_high_samples": 5, "memory": mem})
        self.assertEqual([lvl for lvl, _ in w], ["warning", "error"])
        self.assertEqual(monitor.warnings_for({}), [])


class QuickParsersTest(unittest.TestCase):
    def test_wpctl(self):
        self.assertEqual(quick.parse_wpctl_volume("Volume: 0.40\n"), (40, False))
        self.assertEqual(quick.parse_wpctl_volume("Volume: 1.00 [MUTED]\n"), (100, True))
        with self.assertRaises(ValueError):
            quick.parse_wpctl_volume("garbage")

    def test_brightnessctl(self):
        self.assertEqual(quick.parse_brightnessctl("intel_backlight,backlight,500,50%,1000\n"), 50)
        with self.assertRaises(ValueError):
            quick.parse_brightnessctl("input0::capslock,leds,0,0%,1\n")

    def test_nmcli(self):
        self.assertEqual(quick.parse_nmcli_general("enabled:disabled\n"), (True, False))
        devs = quick.parse_nmcli_devices("wlan0:wifi:connected:Home\\: 5G\neth0:ethernet:unavailable:\n")
        self.assertEqual(devs[0]["connection"], "Home: 5G")
        self.assertEqual(devs[1]["type"], "ethernet")

    def test_bluetoothctl(self):
        self.assertEqual(quick.parse_bluetoothctl_show("Controller 00:11 x\n\tPowered: yes\n"), (True, True))
        self.assertEqual(quick.parse_bluetoothctl_show("No default controller available\n"), (False, False))

    def test_power_profile(self):
        self.assertEqual(quick.parse_power_profile('{"text": "x", "tooltip": "TLP profile: powersave (click)"}'),
                         "powersave")
        self.assertEqual(quick.parse_power_profile("not json"), "balanced")

    def test_missing_tool_is_unavailable(self):
        with self.assertRaises(quick.Unavailable):
            quick.run(["luxenos-definitely-not-installed"])


if __name__ == "__main__":
    unittest.main()
