# Boot problems

_Last updated: 2026-10-11_

## Summary
The reported installer problem ("Calamares doesn't see all the space on the
drive and has no administrator rights") has one root cause: the installer
kiosk ran plain `exec calamares` as the unprivileged live user. Nothing in
LuxenOS elevated it (no sudo/pkexec wrapper). Calamares then fails its
`root` requirement on the welcome page and cannot read the disks properly.
Calamares is now started through a new `luxenos-installer` wrapper, which
runs it as root. On the live USB its root access comes from a live-only
boot service of LuxenOS's own, not from live-config, and a visible error
replaces the old silent failure.

Walking the rest of the chain turned up a second problem that blocks a
finished install: the image contained **no GRUB at all**. Calamares'
`bootloader` job runs `grub-install` inside the copied root filesystem, so it
would fail at the end of every install. GRUB packages are now in the package
lists. A smaller problem: the Calamares slideshow could not load (Qt 6
Calamares, Qt 5 QML modules). No ISO exists in the repo and `lb build` was
not allowed, so **none of this has been verified by a real boot or
install**. All checks were static, plus inspection of the actual trixie
`.deb`s (calamares 3.3.14-1, calamares-settings-debian 13.0.13-1, live-config
11.0.5, libkpmcore12 24.12.3-2, grub 2.12-9+deb13u2) and live-build
1:20250505+deb13u1's scripts.

## Findings

### 1. Calamares ran without root rights: no admin rights, disks/space not seen — status: fixed
- **Stage:** installer
- **Severity:** blocks boot (no install possible)
- **Where:** `config/includes.chroot/etc/luxenos-installer/kiosk-sway-config:18,26` (`exec calamares`, `bindsym $mod+Shift+r exec calamares`), `config/includes.chroot/etc/skel/.config/sway/config:68` (`Super+Shift+I`)
- **What:** The kiosk Sway session runs as the autologin live user (uid 1000). Sway's `exec calamares` therefore started Calamares unprivileged. `grep -rn 'sudo\|pkexec' config` found no elevation anywhere on the installer path.
  - LuxenOS's `welcome.conf` lists `root` under `required`, so the welcome page reports missing administrator rights and blocks Next.
  - The partition page enumerates devices and free space through kpmcore/libparted, which need root to read the block devices. Unprivileged, disks and unallocated space are missing or wrong. That matches "doesn't see all the space".
  - Debian itself never runs Calamares unprivileged. `calamares-settings-debian`'s `/usr/bin/calamares-install-debian` does `pkexec calamares`. The calamares package's own `calamares.desktop` does `sh -c "pkexec calamares"`, and calamares ships polkit action `com.github.calamares.calamares.pkexec.run` (auth_admin).
  - The privileges themselves already exist on the live system. live-config 11.0.5 component `0040-sudo` writes `/etc/sudoers.d/live` (`user ALL=(ALL) NOPASSWD: ALL`). Component `1080-policykit` writes `/usr/share/polkit-1/rules.d/sudo_on_live.rules`, which returns YES for the active local live user. Neither was ever used.
- **Fix:** New `config/includes.chroot/usr/local/bin/luxenos-installer`, hardened in a second pass (see finding 6). The kiosk `exec` and `Super+Shift+R`, plus the desktop's `Super+Shift+I`, now call it.
  - `/usr/local/share/applications/calamares.desktop` overrides the package launcher (same ID, earlier in XDG_DATA_DIRS) as "Install LuxenOS" → `luxenos-installer`. Debian's version runs `pkexec calamares`, which drops `WAYLAND_DISPLAY` and cannot open a window under Sway.
  - `calamares-install-debian.desktop` is hidden: it needs `xhost`, which is not installed.
  - `luxenos-health-check` text updated. `tools/validate-release-tree` now fails if a Sway config runs `calamares` directly.
- **Verified by:** static check only (`sh -n`, shellcheck 0.10.0, validate-release-tree, `desktop-file-validate`) plus the fake-sudo/pkexec tests in finding 6. Not boot-verified.

### 2. No GRUB in the image: Calamares `bootloader` job cannot install a bootloader — status: fixed
- **Stage:** installer → installed system
- **Severity:** blocks boot (install fails at the bootloader step; nothing bootable on disk)
- **Where:** `config/package-lists/*.list.chroot` (no `grub-*`), `config/includes.chroot/etc/calamares/settings.conf` (no `bootloader-config`)
- **What:** Calamares' `bootloader` module (`/usr/lib/x86_64-linux-gnu/calamares/modules/bootloader/main.py`) runs `grub-install --target=x86_64-efi|i386-pc` in the target, so GRUB must be in the copied squashfs.
  - live-build only installs its GRUB/syslinux packages temporarily for the ISO's own boot menu. `binary_grub-efi` calls `Install_packages` then `Remove_packages`, and `lb binary` runs `binary_rootfs` (squashfs) at line 48, before `binary_grub-efi` at line 64.
  - Debian's settings avoid this with the `bootloader-config` job, which `apt-get install`s grub-pc or grub-efi from the ISO pool. LuxenOS dropped that job, has no pool, and builds with `--apt-indices false`.
- **Fix:** `installer.list.chroot` gains `grub-pc` (BIOS; `grub-bios-setup` lives only in this metapackage), `grub-efi-amd64-bin` (UEFI modules; doesn't conflict with grub-pc, unlike the `grub-efi-amd64` metapackage) and `efibootmgr`.
  - Checked from the grub-pc postinst: on a fresh install in the build chroot it skips grub-install (no core.img) and update-grub (no grub.cfg), so it won't break the build.
  - Calamares' `grubcfg` handles `/etc/default/grub`. It now exists (from grub-pc), which also makes the GRUB theme from hook 0200 actually apply on installed systems. Before, the `-f /etc/default/grub` check was always false.
  - The cleanup shellprocess now seeds `grub-pc/install_devices` in debconf on BIOS installs, using the `/dev/disk/by-id` path of `grub-probe -t disk /boot/grub`. Otherwise every later grub-pc upgrade would fail with "You must correct your GRUB install devices" (fatal under unattended-upgrades).
  - `validate-release-tree` checks the two packages are listed.
- **Verified by:** static check (package names exist in trixie, `.deb` contents and postinst inspected, `sh -n` on the shellprocess command). Not boot-verified.

### 3. Hook 0200 ran `update-grub` in the build chroot — status: fixed
- **Stage:** build
- **Severity:** degrades boot (would probe the build host's disks / leave a host-specific grub.cfg in the image now that GRUB is installed)
- **Where:** `config/hooks/live/0200-configure-premium-defaults.hook.chroot` (GRUB theme block)
- **What:** The block never ran before (no `/etc/default/grub`). With grub-pc it would run `update-grub` inside the chroot.
- **Fix:** It now only writes `GRUB_THEME=` and lets Calamares generate `grub.cfg` on the target.
- **Verified by:** `sh -n`.

### 4. Calamares slideshow could not load (Qt 6 Calamares, Qt 5 QML modules) — status: fixed
- **Stage:** installer
- **Severity:** cosmetic (blank area during installation; logged QML error)
- **Where:** `config/package-lists/installer.list.chroot`
- **What:** Trixie's calamares links Qt 6 (`libqt6quick6` …). The list carried Qt 5 `qml-module-qtquick-controls2/-layouts/-qtgraphicaleffects`, which Qt 6 cannot use. `show.qml` imports `QtQuick`.
- **Fix:** Replaced them with `qml6-module-qtquick` and `qml6-module-qtquick-window`. calamares-settings-debian still Depends on the Qt 5 qtquick2/window2 itself.
- **Verified by:** package lookup (`apt-cache policy`). Not boot-verified.

### 5. Sway exits on machines/VMs with only software rendering — status: fixed (needs-hardware-test)
- **Stage:** live session / installed system
- **Severity:** blocks boot (on affected machines: console only, no installer)
- **Where:** `config/includes.chroot/etc/profile.d/luxenos-session.sh`
- **What:** wlroots refuses llvmpipe unless `WLR_RENDERER_ALLOW_SOFTWARE=1`. That covers QEMU/VirtualBox testing and GPUs without a driver.
- **Fix:** The variable is now exported before `exec sway`. It has no effect when a hardware renderer exists. `WLR_NO_HARDWARE_CURSORS=1` is also set when `systemd-detect-virt --vm` succeeds.
- **Verified by:** `sh -n`, `dash -n`. Not boot-verified.

### 6. Installer root access depended on live-config and had a silent failure path — status: fixed
- **Stage:** live session / installer
- **Severity:** blocks boot (on failure the kiosk showed a blank screen without Calamares)
- **Where:** `config/includes.chroot/usr/local/bin/luxenos-installer` (first version)
- **What:** Four weaknesses in the first version:
  - It relied entirely on live-config's runtime `/etc/sudoers.d/live`; the repo shipped no sudoers or polkit rule of its own.
  - It used `sudo --preserve-env=…`, which sudoers policy may refuse.
  - Its pkexec fallback needs a polkit agent, and the kiosk ran none.
  - If both failed, nothing was shown.
- **Fix:**
  - **Live-only privilege path:** new `luxenos-live-access.service` (`ConditionKernelCommandLine=boot=live`, `ConditionPathIsDirectory=/run/live`, `Before=getty@tty1.service`), enabled by hook 0150. At boot it runs `/usr/libexec/luxenos/luxenos-live-access`, which writes two files into the live RAM overlay:
    - `/etc/sudoers.d/luxenos-live-installer`: `<user> ALL=(root) NOPASSWD: /usr/local/bin/luxenos-installer`, checked with `visudo -c` before being renamed into place.
    - `/etc/polkit-1/rules.d/49-luxenos-live-installer.rules`: YES only for `org.freedesktop.policykit.exec` with `program == /usr/local/bin/luxenos-installer` for that local user.
    - The user comes from the `username=` boot parameter, else the build-time name in `/etc/luxenos-installer/live-username`. `root` and invalid names are refused.
  - **Why it can't reach installed systems:**
    - Nothing that grants root is shipped statically; validate-release-tree fails if `includes.chroot` gains `etc/sudoers.d` or `etc/polkit-1/rules.d`.
    - The generated files live only in the overlay, which unpackfs never copies (it copies the squashfs).
    - The Calamares cleanup job deletes the unit, its `multi-user.target.wants` link and the script. It also deletes, defensively, both generated files and live-config's `sudoers.d/live` and `sudo_on_live.rules`.
    - Even with `boot=live` typed at GRUB, the unit also needs live-boot's `/run/live`.
  - **Launcher passes no environment:** the Wayland socket *name* is passed as an argument (`--wayland-display=`, only `[A-Za-z0-9._-]`).
    - The root side takes the uid from `SUDO_UID`/`PKEXEC_UID` and connects by absolute path `/run/user/<uid>/<name>` (root bypasses the 0700 runtime dir).
    - It sets `QT_QPA_PLATFORM=wayland`, with `xcb` and a validated `--display=` as the fallback.
    - It gives root its own `XDG_RUNTIME_DIR=/run/user/0`, and reads `LANG` from `/etc/default/locale`.
    - Unknown arguments are ignored and nothing is forwarded to Calamares, because the script is runnable as root via sudo/polkit.
  - **Order:**
    1. `sudo -n -l` checks that the exact command is allowed, then `sudo -n luxenos-installer --token=… --wayland-display=…`.
    2. `pkexec luxenos-installer …`, starting `luxenos-polkit-agent` first if no agent runs. The kiosk Sway config now also starts the agent.
    3. A visible error: swaynag (Retry / Open terminal), else zenity, else foot. It is logged with `logger -t luxenos-installer` and to `~/.cache/luxenos-installer.log`.
  - A one-time token marker in `/run/luxenos-installer/` (root-only directory) tells "the root side started" apart from "escalation failed". Calamares' own exit status is therefore never mistaken for a sudo/pkexec failure and never triggers a second launch.
- **Verified by:** shellcheck 0.10.0 (clean, `-s sh`), `sh -n`/`dash -n`, `visudo -cf` (trixie's visudo from the .deb) on a generated sudoers file, JS parse of the generated polkit rule, and validate-release-tree. A sandboxed copy of the launcher was run with fake `id`/`sudo`/`pkexec`/`calamares`/`swaynag`/`zenity` (fake sudo/pkexec clear the environment as the real ones do). Results:
  - sudo works: Calamares runs as uid 0 with `WAYLAND_DISPLAY=/run/user/1000/wayland-1`, `QT_QPA_PLATFORM=wayland`, `XDG_RUNTIME_DIR=/run/user/0`, LANG from the locale file. An exit status of 3 is propagated.
  - sudo refuses env preservation: still works (nothing is preserved).
  - sudo denied, or sudo not installed, with pkexec working: runs via pkexec (`PKEXEC_UID`), and the agent is started.
  - Everything fails (pkexec 126/127): swaynag error (zenity when there is no SWAYSOCK), log entry, exit 1.
  - An injected socket name (`../../0/x`) is rejected and falls back to xcb.
  - luxenos-live-access in a sandbox: no-op without `boot=live`, honours `username=`, refuses `root` and `a*b`.
  - Not boot-verified: real sudo/polkitd/Sway behaviour, and the unit ordering against autologin.

## Other interesting observations
- **No partition.conf is a non-issue.** Neither calamares nor calamares-settings-debian ships one, so Calamares' built-in defaults apply (EFI at `/boot/efi`, ext4, swap partition sized from RAM). Debian's live images work the same way. With the default swap choice the root partition is smaller than the disk by the swap size. That is expected, not "missing space".
- **requiredStorage 10 GiB** (`welcome.conf`) is below Debian's 15 and fine. It only gates the welcome page; it does not limit what the partition page shows.
- **Partitioning tools present:** parted, gparted, dosfstools, e2fsprogs, btrfs-progs, ntfs-3g, cryptsetup. kpmcore comes via calamares' Depends. os-prober is a calamares Depends.
- **Marker files:** each launch leaves one empty token file in `/run/luxenos-installer/` (tmpfs, root-owned). This is intentional, so that even an instantly exiting Calamares is recognised as "root side started".
- **UEFI installs keep grub-pc** installed only for its files. Its postinst ignores them (no i386-pc core.img), so GRUB EFI on disk is never refreshed by package upgrades. It stays consistent with its own modules in `/boot/grub/x86_64-efi` but misses GRUB security updates. Proper fix (open): on UEFI, have the cleanup job swap grub-pc for a cached `grub-efi-amd64` .deb, as Debian's `bootloader-config` does from the pool.
- **No Secure Boot:** the installed GRUB is unsigned (no shim/grub-efi-amd64-signed). It won't boot with Secure Boot enabled. This is already listed in README "Known limitations".
- **GRUB theme `theme.txt`** asks for "Noto Sans Bold 24" and `select_*.png`. Neither a `.pf2` font nor those pixmaps are shipped, so GRUB falls back to its default font and draws no selection pixmap. Cosmetic.
- **Debian's sequence differs from LuxenOS's:** LuxenOS drops `luksbootkeyfile`, `services-systemd`, `plymouthcfg`, `initramfscfg`/`initramfs` (replaced by the shellprocess `update-initramfs`), and `packages`. The fstab module only writes `/crypto_keyfile.bin` into crypttab if that file exists (`fstab/main.py:156`), so dropping `luksbootkeyfile` just means typing the LUKS passphrase twice; it does not break boot.
- **Live-build fstab:** the `chroot_hacks` fstab truncation only applies to `--debian-installer live`. At live boot, live-boot writes the overlay fstab, which is why the wrapper moves it aside.

## How to reproduce / test
```sh
# static checks
sh tools/validate-release-tree
sh -n config/includes.chroot/usr/local/bin/luxenos-installer
desktop-file-validate config/includes.chroot/usr/local/share/applications/*.desktop

# inspect what Debian ships (no root needed)
mkdir debs && cd debs && apt-get download calamares calamares-settings-debian live-config grub-pc
for p in *.deb; do mkdir -p x/${p%%_*}; dpkg-deb -x $p x/${p%%_*}; done
cat x/calamares-settings-debian/usr/bin/calamares-install-debian   # pkexec calamares
cat x/live-config/usr/lib/live/config/0040-sudo                    # NOPASSWD sudo for live user

# real test (needs root + network): build, then boot in QEMU
sudo lb clean && ./auto/config && sudo lb build
qemu-system-x86_64 -enable-kvm -m 4G -cdrom live-image-amd64.hybrid.iso \
  -drive file=disk.qcow2,if=virtio -serial stdio          # BIOS
# add: -bios /usr/share/ovmf/OVMF.fd                      # UEFI
# In the kiosk: Super+Return -> `ps -o user,cmd -C calamares` must show root;
# partition page must list the whole virtio disk; finish install, reboot from disk.
```
