import os
import tempfile
import unittest

import helpers
from luxenos import hardware, snapshots, updates

assert helpers.CHROOT

APT_SIM = """Reading package lists...
Inst libc6 [2.41-11] (2.41-12 Debian-Security:13/stable-security [amd64])
Inst firefox-esr [140.1esr-1] (140.2esr-1 Debian:13.2/stable [amd64])
Inst linux-image-6.12.50-amd64 (6.12.50-1 Debian:13.2/stable [amd64])
Inst waydroid [1.5.0] (1.5.1 waydroid:bookworm [amd64])
Remv oldpkg [1.0]
Conf libc6 (2.41-12 Debian-Security:13/stable-security [amd64])
E: Something odd
"""


class UpdatesTest(unittest.TestCase):
    def test_apt_simulation(self):
        ups, new, removed, errors = updates.parse_apt_upgrades(APT_SIM)
        by = {u.name: u for u in ups}
        self.assertTrue(by["libc6"].security)
        self.assertFalse(by["firefox-esr"].security)
        self.assertEqual(by["waydroid"].source, "android")
        self.assertEqual((by["libc6"].old, by["libc6"].new), ("2.41-11", "2.41-12"))
        self.assertEqual(new, ["linux-image-6.12.50-amd64"])
        self.assertEqual(removed, ["oldpkg"])
        self.assertEqual(errors, ["Something odd"])
        self.assertIn("1 of them security", updates.notify_summary(ups))
        self.assertIsNone(updates.notify_summary([]))

    def test_flatpak_and_fwupd(self):
        fp = updates.parse_flatpak_updates("org.gimp.GIMP\t2.10.38\tflathub\n\n", "system")
        self.assertEqual((fp[0].name, fp[0].new), ("org.gimp.GIMP", "2.10.38"))
        self.assertIn("system", fp[0].detail)
        fw = updates.parse_fwupd_updates('{"Devices": [{"Name": "UEFI", "Version": "1.0", "Releases": '
                                         '[{"Version": "1.2", "Summary": "Fixes"}]}, {"Name": "Other"}]}')
        self.assertEqual([(u.name, u.old, u.new) for u in fw], [("UEFI", "1.0", "1.2")])
        self.assertEqual(updates.parse_fwupd_updates("garbage"), [])

    def test_history(self):
        text = ("Start-Date: 2026-10-01  10:00:00\nCommandline: apt-get upgrade\n"
                "Upgrade: a:amd64 (1, 2), b:amd64 (3, 4)\nEnd-Date: 2026-10-01  10:01:00\n\n"
                "Start-Date: 2026-10-02  09:00:00\nCommandline: apt-get install x\nInstall: x:amd64 (1)\n"
                "Error: Sub-process returned an error code\nEnd-Date: x\n")
        h = updates.parse_apt_history(text)
        self.assertEqual(h[0]["installed"], 1)
        self.assertTrue(h[0]["error"])
        self.assertEqual(h[1]["upgraded"], 2)

    def test_schedule_validation(self):
        cur = {"ENABLE_AUTO_UPDATES": "no", "AUTO_UPDATE_TIME": "02:00"}
        self.assertEqual(updates.schedule_changes(cur, {"ENABLE_AUTO_UPDATES": "yes", "AUTO_UPDATE_TIME": "02:00"}),
                         ["ENABLE_AUTO_UPDATES=yes"])
        for bad in ({"AUTO_UPDATE_TIME": "25:00"}, {"ENABLE_AUTO_UPDATES": "maybe"}, {"EVIL": "yes"}):
            with self.assertRaises(ValueError):
                updates.schedule_changes(cur, bad)

    def test_schedule_keys_match_root_helper(self):
        with open(os.path.join(helpers.CHROOT, "usr/libexec/luxenos/luxenos-pkg-root"), encoding="utf-8") as f:
            helper = f.read()
        for key, _l, _k, _d in updates.SCHEDULE_SETTINGS:
            self.assertIn(key, helper, "luxenos-pkg-root set-update-schedule must accept %s" % key)

    def test_lists_age(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(updates.lists_age(d))
            p = os.path.join(d, "deb.debian.org_InRelease")
            open(p, "w").close()
            os.utime(p, (1000, 1000))
            self.assertEqual(updates.lists_age(d, now=4600), 3600)
        self.assertEqual(updates.age_text(None), "never")
        self.assertEqual(updates.age_text(7200), "2 hours ago")


class SnapshotsTest(unittest.TestCase):
    def test_listing(self):
        with tempfile.TemporaryDirectory() as d:
            for ident, trig in (("luxenos-snap-20261001-100000", "update"), ("luxenos-snap-20261002-100000", "manual"),
                                ("luxenos-snap-20261003-100000", "update")):
                os.mkdir(os.path.join(d, ident))
                with open(os.path.join(d, ident + ".info"), "w") as f:
                    f.write("FORMAT=1\nCREATED=1\nDESCRIPTION=x\nTRIGGER=%s\n" % trig)
            os.mkdir(os.path.join(d, "not-ours"))
            snaps = snapshots.list_snapshots(d)
            self.assertEqual([s.id[-15:] for s in snaps], ["20261003-100000", "20261002-100000", "20261001-100000"])
            old = snapshots.over_retention(snaps, 1)
            self.assertEqual([s.id for s in old], ["luxenos-snap-20261001-100000"])  # manual ones never pruned

    def test_status(self):
        st = snapshots.parse_status(["@@ITEM fstype btrfs", "@@ITEM rollback no /home is part of the system", "x"])
        self.assertEqual(snapshots.rollback_verdict(st), (False, "/home is part of the system"))
        self.assertEqual(snapshots.rollback_verdict({"rollback": "yes"}), (True, ""))


LSPCI = """Slot:	00:02.0
Class:	VGA compatible controller [0300]
Vendor:	Intel Corporation [8086]
Device:	HD Graphics 4600 [0416]
Driver:	i915
Module:	i915

Slot:	01:00.0
Class:	Network controller [0280]
Vendor:	Intel Corporation [8086]
Device:	Wireless 7260 [08b1]
Module:	iwlwifi
"""


class HardwareTest(unittest.TestCase):
    def test_cpuinfo(self):
        text = ("processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Core(TM)  i5\nflags\t\t: fpu vmx\n"
                "physical id\t: 0\ncore id\t\t: 0\n\nprocessor\t: 1\nphysical id\t: 0\ncore id\t\t: 0\n\n"
                "processor\t: 2\nphysical id\t: 0\ncore id\t\t: 1\n")
        c = hardware.parse_cpuinfo(text)
        self.assertEqual((c["threads"], c["cores"], c["model"]), (3, 2, "Intel(R) Core(TM) i5"))
        self.assertEqual(hardware.virtualization(c["flags"]), "Intel VT-x")

    def test_pci_usb_disks_audio(self):
        devs = hardware.parse_lspci_vmm(LSPCI)
        self.assertEqual(devs[0]["driver"], "i915")
        self.assertNotIn("driver", devs[1])
        self.assertEqual(hardware.strip_ids(devs[0]["vendor"]), "Intel Corporation")
        usb = hardware.parse_lsusb("Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
                                   "Bus 001 Device 003: ID 0c45:64d0 Microdia Integrated Webcam\n")
        self.assertEqual([d["name"] for d in usb if not hardware.is_usb_hub(d)], ["Microdia Integrated Webcam"])
        disks = hardware.parse_lsblk('{"blockdevices": [{"name": "sda", "model": "WDC ", "size": 500, "tran": "sata",'
                                     ' "rota": true, "rm": false, "type": "disk"}, {"name": "loop0", "type": "loop"}]}')
        self.assertEqual([(d["name"], d["kind"], d["model"]) for d in disks], [("sda", "HDD", "WDC")])
        self.assertEqual(hardware.parse_asound_cards(" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
                                                     "                      HDA Intel PCH at 0xf7d1 irq 33\n"),
                         ["HDA-Intel - HDA Intel PCH"])

    def test_checks(self):
        gpus = [{"device": "GPU [1]", "driver": ""}]
        out = hardware.checks({"flags": set()}, 2 * 1024 ** 3, gpus, [], {"uefi": True, "secure_boot": None}, False)
        levels = {t: l for l, t, _a in out}
        self.assertEqual(levels["No driver for GPU"], "problem")
        self.assertIn("No CPU virtualization flag visible", levels)
        self.assertIn("No Wi-Fi adapter found", levels)

    def test_secure_boot_variable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(hardware.boot_mode(d), {"uefi": False, "secure_boot": None, "setup_mode": None})
            ev = os.path.join(d, "firmware", "efi", "efivars")
            os.makedirs(ev)
            with open(os.path.join(ev, "SecureBoot-" + hardware.EFI_GLOBAL), "wb") as f:
                f.write(b"\x06\x00\x00\x00\x01")
            with open(os.path.join(ev, "SetupMode-" + hardware.EFI_GLOBAL), "wb") as f:
                f.write(b"\x06\x00\x00\x00\x00")
            self.assertEqual(hardware.boot_mode(d), {"uefi": True, "secure_boot": True, "setup_mode": False})


if __name__ == "__main__":
    unittest.main()
