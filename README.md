# LuxenOS
## Status

This project is currently in active beta. The desktop and login flow are under active testing; use the images for evaluation and feedback rather than production deployment.

A free, open-source Linux desktop for x86_64 laptops that runs real Android
apps natively (Waydroid), with a translucent "liquid glass" Sway desktop.
You boot a USB stick, install with Calamares, and log in to Sway.

## What this is (and isn't)

LuxenOS is a **Debian remix**, built with `live-build`: Debian 13 "Trixie"
underneath (Debian 14 "Forky" can be built experimentally, see
[Debian 14](#debian-14-forky)), with a curated Sway/Wayland desktop, Waydroid for Android apps,
and a graphical installer. It is not a kernel or userland written from zero -
no serious general-purpose desktop OS is, realistically, without a multi-year
team effort. What *is* built here, specifically for this project: the desktop
configuration and glass theme, the Android integration and its first-boot
setup flow, the installer session, the branding and boot behavior, and the
hardening layered on top of Debian.

The build produces an `iso-hybrid` ISO in one of three variants: two
**live/installer hybrids** (`installer`, the default, and `live`) and a
**traditional, non-live installer** (`traditional`) that boots the Debian
installer straight from the USB stick, with no live session. See
[How LuxenOS boots](#how-luxenos-boots) for exactly what happens in the live
session, in each installer, and on the installed system.

## Core functions

### Android app compatibility
[Waydroid](https://waydroid.org/) runs a real Android container alongside
your Linux desktop; Android apps appear as regular windows you can resize,
snap, and switch between like anything else. Set up by
`config/hooks/live/0100-install-waydroid.hook.chroot`:
- Downloads and configures the Waydroid repo/package at build time.
- Can bundle Aurora Store when `AURORA_STORE_URL` and its matching
  `AURORA_STORE_SHA256` are provided at build time. Defaults pin Aurora Store
  4.8.4 (code 76) from IzzyOnDroid; the build verifies its SHA-256 before bundling it.
- Creates a one-shot systemd service that initializes the Android image on
  the *installed* system's first real boot.
- `luxenos-setup-android` (also in the app launcher as "Android Setup")
  waits for first-boot initialization, starts the Waydroid session, verifies
  it reaches RUNNING, and installs Aurora Store only if the APK was bundled.

### The desktop
Sway (tiling Wayland compositor) + Waybar, configured under
`config/includes.chroot/etc/skel/.config/`, so every new user account gets
it by default. Includes screenshot/annotation (`grim`/`slurp`/`swappy`), a
power-profile switcher tied to `tlp`, GTK theming via `nwg-look`, and one
shared palette (the glass theme) across Sway, Waybar, wofi, dunst, the lock
screen and GTK accents. `luxenos-session-init` starts the polkit agent,
notifications and the network tray once per login.

### Liquid glass
One coherent translucent look - floating rounded Waybar pills, a glass
launcher, glass notifications, thin light borders, soft highlights - tuned
from one file: `/etc/luxenos/glass.conf`.

| | Stock Sway fallback | SwayFX (bundled by the ISO build) |
| --- | --- | --- |
| Translucent panels, borders, accent, sheen | yes | yes |
| Rounded panels (Waybar/wofi/dunst/GTK) | yes (CSS) | yes |
| Blur behind panels | no (opacity is raised to stay readable) | yes |
| Rounded *window* corners, window shadows | no | yes |
| Layer effects (bar/launcher/notification blur + shadows) | no | yes |
| Dimmed inactive windows | no | yes (`rich`) |

The ISO build compiles SwayFX 0.5.3 with its matching SceneFX 0.4.1 and wlroots
0.19.0 in `config/hooks/live/0200-build-swayfx.hook.chroot`. The hook verifies
the resulting compositor and fails the image build if SwayFX was not installed.
Stock Debian Sway remains installed as a recovery/fallback binary. At runtime,
`luxenos-glass` asks the *running* compositor whether it accepts SwayFX-only
commands, and applies every setting independently, so one unsupported effect
never stops the rest. Text is never left unreadable:
panel opacity is raised until text keeps the configured contrast
(`GLASS_MIN_CONTRAST`) over the worst-case backdrop, whatever the preset.

**Presets** (`GLASS_PRESET`, or `luxenos-glass preset ...`; Super+C menu):

| Preset | Blur | Shadows | Panel opacity | Animation | Extras |
| --- | --- | --- | --- | --- | --- |
| `performance` | off | off | 88% | none | radius 8, gaps 4 - for old GPUs (e.g. Dell Latitude E6440) |
| `balanced` (default) | radius 5, 2 passes | soft | 68% | 120 ms | radius 12, gaps 6 |
| `rich` | radius 8, 3 passes | deep | 55% | 200 ms | radius 16, punchier blur colour, inactive windows dimmed |
| `flat` (command only) | off | off | 100% | none | no glass at all |

**Graphical accessibility settings.** Open **Super+C → Settings: Accessibility** or launch **Accessibility Settings** from the app launcher. The 0–100 Liquid Glass slider adjusts opacity, blur strength, border visibility, shadows, corner radius, saturation, spacing and animation. Lower values are simpler and lighter on the GPU; 0 disables glass, while 100 applies the strongest available treatment. The same screen has a **Reduce motion** toggle and a reset option. Changes are saved per user and applied immediately; the system defaults in `/etc/luxenos/glass.conf` remain unchanged. Real compositor blur and rounded window corners require SwayFX; stock Sway fallback keeps panels more opaque for readability.

**Reduced motion.** `GLASS_REDUCE_MOTION=yes` sets all transitions to zero
(bar, launcher, GTK `enable-animations`). Hover and focus colours still change
instantly, so feedback is kept.

```sh
luxenos-glass status                  # what is active, and how SwayFX was detected
luxenos-glass preset performance      # or balanced / rich / flat
luxenos-glass theme light             # or dark
luxenos-glass set GLASS_REDUCE_MOTION yes
luxenos-glass set GLASS_ACCENT '#e5a24a'
luxenos-glass apply --debug           # reapply and print why anything was skipped
luxenos-glass namespaces              # layer-shell namespaces used for SwayFX effects
luxenos-glass reset                   # drop your overrides
```

### Double-click `.deb` and `.exe`
- **`.deb`**: opens `luxenos-deb-install`: shows name/version/architecture and
  what apt would also install or remove, asks for confirmation, authenticates
  with polkit, installs through `apt-get` (dependencies resolved by apt), with
  a progress bar and a clear result. No terminal window.
- **`.exe` / `.msi`**: opens `luxenos-wine-open`: validates the file, creates a
  per-user `~/.wine` on first use, runs it with Debian's Wine, and reports
  problems in dialogs. Never runs as root.
- Both are registered as the default handlers in `/etc/xdg/mimeapps.list` and
  appear under "Open With" in Thunar. Logs are never touched; installs are also
  recorded in `/var/log/luxenos/pkg-helper.log`.

### Desktop apps
Native LuxenOS apps (Python 3 + GTK 4, code in
`config/includes.chroot/usr/lib/luxenos/python/luxenos/`). They share one look,
generated by `luxenos-glass` from `glass.conf` and Settings > Accessibility
(`~/.config/luxenos/glass/shell.css`), and honour Reduce motion. The panels
(launcher, dock, Quick Settings, overview) are wlr-layer-shell surfaces that
SwayFX blurs; there is no X11 dependency.

| App | Open with | What it does |
| --- | --- | --- |
| Launcher | Super+Space | Universal search: installed apps (typo-tolerant), settings pages, calculator ("2*(3+4)"), unit conversion ("10 km to mi"), web search shortcuts ("wiki …", "ddg …"), recent searches (can be cleared or turned off), launcher plugins, quick actions (settings, lock, log out, restart, shut down; destructive ones need a second Enter), recent documents and files (`plocate`, or a bounded scan of your folders). Executables found by search are never run: their folder opens instead. |
| Dock | always on, Super+Shift+D focuses it | Pinned and running apps from Sway IPC events; click to start/focus, click the focused app to minimize (Sway scratchpad) and again to restore; several windows cycle; right click lists every window. One per monitor, single instance. Settings > Dock or `luxenos-dock config`. |
| Quick Settings | Super+N, slider icon in Waybar | Wi-Fi (NetworkManager), Bluetooth (BlueZ), volume (WirePlumber), brightness, night light (wlsunset), Do Not Disturb (dunst), battery, power profile. Real state, refreshed while open; missing hardware shows as unavailable. |
| Workspace overview | Super+Tab | All workspaces on all monitors with their windows and live previews of the windows on screen; click to focus, drag (or the window menu) to move a window to another or a new workspace; arrow-key navigation; per-workspace menu to rename, set a wallpaper, or close it (its windows move to a neighbour). |
| System Monitor | Ctrl+Shift+Esc | CPU, memory, network and battery graphs, processes (ending one always asks; other users' processes need an administrator), disk space, warnings for low disk, memory pressure, sustained CPU load and low battery. |
| Software Center | launcher, dock | Browse/search the Debian catalogue by category, installed software, updates. Shows apt's own preview of every change and where each package comes from (Debian / third-party / local). Changes run through `luxenos-pkg-root` with an administrator password; apt keeps verifying signatures. `.deb` files open in the graphical `.deb` installer. |
| Restore Center | Settings > Restore points | Restore points before updates or by hand, check, preview, restore (see below). |
| Welcome guide | first login, then launcher | Keyboard layout, time zone, network, theme pack and accent, updates, suggested apps, shortcuts. Runs once per user on an installed system, never on the live USB; Skip at any time. |
| Settings | launcher, Super+C | Appearance (theme packs with a 20-second keep/revert preview, light/dark, accent), Accessibility (the existing Liquid Glass page), Dock, and links to the other pages. |
| Personalization | Settings, launcher | Everything about the look in one window: light/dark, accent, theme packs, Liquid Glass preset and intensity, blur, transparency, shadows, corners, borders, gaps, animation length, fonts, panel position, icon and cursor themes, wallpapers and slideshow, installing themes from files. It only writes through `luxenos-glass`, the wallpaper engine and theme packs (no second config system); "Undo this session" and "Reset to defaults". |
| Update Manager | launcher ("Update Manager"), Settings | Debian updates (security updates listed and installable separately), Flatpak, firmware (fwupd) and Android (Waydroid) updates, download-only, update history and logs, automatic-update schedule and notifications. Restore point (and on Btrfs a snapshot) before updating; firmware/Android/removals ask first. One package lock shared with the Software Center and the nightly updater. |
| Hardware | launcher, Settings | CPU, memory, graphics, storage, battery, network, USB, audio, drivers in use, firmware versions, compatibility checks (Android support, missing drivers, boot mode) and Secure Boot status; "Copy report". |
| Notifications | Super+Shift+N, bell in Waybar | dunst history grouped by app ("show again", remove, clear all), Do Not Disturb, per-app allow/silent/block and sound, notification sounds, action buttons of a notification (dunst's menu). |
| Desktop widgets | Settings › Desktop widgets | Clock+calendar, weather (Open-Meteo, no account, configurable provider), CPU, memory, battery, network speed, storage, music controls (MPRIS). Drag to move. |
| Android | launcher, Settings | Start/stop/restart Android, first set-up with progress and image choice, install APKs, open/remove/pin Android apps, Android permission page, display size, storage use, troubleshooting with plain-language explanations. |
| Phone Connect | launcher, Settings | KDE Connect (installed on request): discovery, explicit pairing, send files and clipboard, ring the phone, phone notifications, media controls; firewall opened only for your local network on request. |
| Windows Programs | launcher, Settings | Wine prefixes per program, assignments honoured by the `.exe` double-click handler, Windows version, winecfg, launcher shortcuts, backup and restore of prefixes. |
| Plugins | Settings | Launcher plugins: metadata, permissions, API compatibility, enable/disable. Plugins run sandboxed (bubblewrap) as you, never as root. |
| Assistant | Super+F1, launcher | Offline help: explains errors and commands, finds settings, hardware troubleshooting, system health summary, update problems. Runs nothing without a click; optional local model (Ollama, loopback only). |
| Files | dock, launcher | Thunar with breadcrumbs, tabs and split view (F3), thumbnails, archive browsing/extraction (xarchiver), automatic mounting of USB drives, "Open Terminal Here" and "Install with Software Center" for `.deb`. |

**Theme packs** (`/usr/share/luxenos/themes/*.theme`, your own in
`~/.local/share/luxenos/themes/`): Liquid Glass, Daylight Glass, Minimal,
Performance. A pack sets colours, light/dark and the wallpaper through
`luxenos-glass` and a managed Sway fragment
(`~/.config/luxenos/sway.d/50-wallpaper.conf`); it keeps your Liquid Glass
intensity unless you choose the pack's glass level, and never touches Reduce
motion. `luxenos-theme list|apply ID [--with-glass]|revert`.

**Restore points are configuration backups, not system snapshots.** Each one
holds `/etc` and the list of installed packages (with checksums). Restoring
puts settings back and reinstalls/removes packages to match the list, in
their *current* repository versions. It never touches `/home`, accounts and
passwords, the machine id, `fstab`/`crypttab`, kernels or the core desktop
packages, and it saves the current state as a new restore point first. ext4
has no snapshots. **On Btrfs** the Restore Center also has a Snapshots
page: read-only snapshots of the system (by hand, and before every update
unless `SNAPSHOT_BEFORE_UPDATE=no`), deletion (always confirmed; old
automatic ones only by your choice or `SNAPSHOT_AUTO_PRUNE=yes`), and a
rollback that makes a snapshot the system for the next boot - offered only
when fstab mounts the root by `subvol=`, `/boot` is on it and `/home` is a
separate subvolume, and the previous system is kept on disk. If the
desktop does not start: Ctrl+Alt+F2, log in, `sudo luxenos-restore`.

### Installation
Calamares, with LuxenOS branding
(`config/includes.chroot/etc/calamares/branding/luxenos/`) and support for
full-disk LUKS encryption. The live session boots straight into it; see below.
The `traditional` image uses the Debian installer instead, without a live
session (see [Traditional image](#traditional-image-no-live-session)).

### Hardening
AppArmor with real profile packages (not just the empty service), UFW
default-deny-incoming, sysctl hardening
(`config/includes.chroot/etc/sysctl.d/99-luxenos.conf`), and
`unattended-upgrades` enabled on the installed system.

## How LuxenOS boots

The ISO is a standard `live-build` **live/installer hybrid** (`iso-hybrid`,
BIOS via syslinux and UEFI via GRUB). It is *not* a pre-installed disk image:
the installed system exists only after Calamares has copied it to disk.

The steps below describe the default `installer` image. A `live` image
(`VARIANT=live`, see [Image variants](#image-variants)) is identical except
that step 1 starts the full Sway desktop instead of the installer kiosk. The
`traditional` image skips the live session entirely; see
[Traditional image](#traditional-image-no-live-session).

**1. Live environment (booting the USB without installing).**
The kernel command line contains `boot=live`, so `live-boot` mounts the
read-only squashfs with a RAM overlay and `live-config` creates the live
user (default `user`). tty1 logs that user in automatically (systemd drop-in
from `config/hooks/live/0150-installer-kiosk.hook.chroot`), and
`/etc/profile.d/luxenos-session.sh` starts Sway with the **installer kiosk**
config (`/etc/luxenos-installer/kiosk-sway-config`): no bar, no launcher, just
Calamares, plus `Super+Return` for a terminal and `Super+Shift+R` to
relaunch the installer. To try the full desktop from the USB instead, add
`luxenos.session=desktop` to the kernel command line (press `e` in the
GRUB menu); changes made there are lost at shutdown.

**2. Installer (Calamares).**
Launched by the kiosk session through `luxenos-installer`, which starts
Calamares as root: on the live USB `luxenos-live-access.service` lets the
live user run exactly that command through `sudo`/`pkexec` without a
password (live boots only; removed from the installed system), elsewhere
`pkexec` asks for an administrator password. If it cannot get root it shows
an error instead of an empty screen (`journalctl -t luxenos-installer`). Its sequence
(`config/includes.chroot/etc/calamares/settings.conf`) partitions the disk,
unpacks this same root filesystem (`unpackfs`), creates your account
(`users`), installs GRUB (`grub-pc` for BIOS and `grub-efi-amd64-bin` for
UEFI are shipped in the image for this), then converts the copy into a normal
system: `removeuser` deletes the live account and a `shellprocess` job
(`shellprocess-luxenos-cleanup.conf`) runs
`/usr/libexec/luxenos/luxenos-finish-install`, which purges
`live-boot`/`live-config`, deletes the kiosk files and the live-only root
access, records the BIOS GRUB disk, and rebuilds the initramfs. A final
shellprocess step enables greetd for the installed system.

**3. Installed system.**
No `boot=live`, no kiosk. `greetd` starts the graphical `wlgreet` login screen inside a minimal Sway
compositor on tty1; after authentication, it launches your normal Sway session with your
`~/.config/sway/config` (copied from `/etc/skel`). That config starts
`luxenos-glass`, Waybar, `luxenos-session-init` (polkit agent, dunst,
nm-applet), and the rest. Hardening (AppArmor, UFW,
`unattended-upgrades`) and the Waydroid first-boot service belong to the
installed system.

The installer-to-installed conversion (steps 2-3) has not been exercised in a
real install yet; see "Known limitations".

### Traditional image (no live session)

`VARIANT=traditional` builds a conventional install disc. Nothing runs from
the USB stick except the installer itself:

1. **Boot menu.** BIOS (syslinux) and UEFI (GRUB) both open on
   **Start installer**, followed by *Advanced install options* (graphical,
   text, expert, rescue, dark theme, speech synthesis) and *Utilities*. There
   are no "Live system" entries: `config/hooks/live/0900-traditional-boot-menu.hook.binary`
   removes them from the generated menus (and fails the build if it cannot).
2. **Debian installer (d-i).** live-build's `--debian-installer live` mode:
   d-i's own kernel and initrd boot, and its *live-installer* step copies the
   LuxenOS root filesystem (the same `filesystem.squashfs` the live images
   boot) to disk instead of fetching packages. You choose language, keyboard,
   network, partitioning (guided, LVM, or encrypted LVM) and your account as on
   any Debian install. `config/includes.installer/preseed.cfg` (embedded in
   d-i's initrd) answers only what LuxenOS changes: no tasksel (so Debian's
   GNOME task is not added on top), no root password (your account gets
   sudo), contrib/non-free/non-free-firmware enabled, no leftover `cdrom:`
   apt source, and a `late_command` that runs the same
   `luxenos-finish-install` as Calamares.
3. **Installed system.** Identical to the Calamares install: greetd login
   prompt, Sway, the same hardening and Waydroid first-boot service.

Use it when Calamares fails inside the live session, or on hardware where the
live Sway/kiosk session does not start: d-i needs no graphical session from
the USB stick (the text installer needs no working GPU driver at all). The
installer is Debian's, so it is not LuxenOS-branded.

## How it compares to macOS and Windows

Genuine differentiators, not benchmark claims (we haven't run head-to-head
performance numbers, and you should be skeptical of any distro that claims
to "beat" a mainstream OS without receipts):

- **Native Android apps.** Neither macOS nor Windows runs Android apps
  without a third-party emulator layer; LuxenOS has Waydroid configured out
  of the box.
- **Hardware longevity.** Built and tested against a Dell Latitude E6440 —
  older than current Windows 11's official hardware requirements, and past
  the point macOS supports it at all. LuxenOS keeps that hardware on an
  actively updated desktop.
- **Ownership.** GPLv3-licensed, no telemetry you didn't opt into, no
  subscription required to use your own machine, fully auditable and
  forkable.

## Target

- Hardware: Dell Latitude E6440 and similar amd64 laptops
- Base: Debian 13 "Trixie"
- Desktop (installed system and `luxenos.session=desktop`): Sway + Waybar
- Android runtime: Waydroid, GAPPS or VANILLA image, Aurora Store fallback
- Boot: BIOS (syslinux) and UEFI (`grub-efi`); Secure Boot opt-in
  (`LUXENOS_SECURE_BOOT=1`, untested, see [Secure Boot](#secure-boot))

## Requirements to build

```sh
sudo apt update
sudo apt install live-build curl ripgrep
```

## Environment variables

Read by `config/hooks/live/0100-install-waydroid.hook.chroot` (defaults are
used where a value is missing, and the hook logs the effective image type):

| Variable | Purpose | Default |
| --- | --- | --- |
| `WAYDROID_VERSION` | Version string, logged only | `unspecified` |
| `WAYDROID_IMAGE_TYPE` | `GAPPS` or `VANILLA` | `GAPPS` (build fails on any other value) |
| `WAYDROID_REPO_URL` | URL of the Waydroid repo-setup script | `https://repo.waydro.id` |
| `WAYDROID_INSTALL_SCRIPT` | Local path to save that script to | `/tmp/waydroid-repo-setup.sh` |
| `AURORA_STORE_URL` | URL to download the Aurora Store APK from | Defaults to the pinned IzzyOnDroid APK |
| `AURORA_STORE_DIR` | Directory for the downloaded APK | `/usr/share/luxenos/apks` |
| `AURORA_STORE_SHA256` | Expected SHA-256 of the APK | **Required for release builds** when `AURORA_STORE_URL` is set |
| `LUXENOS_ALLOW_UNPINNED_DOWNLOADS` | `1` to build without `AURORA_STORE_SHA256` | Dev builds only |
| `DOWNLOAD_RETRIES` | Retry count for network downloads | `3` |
| `CURL_TIMEOUT` | Per-attempt curl timeout (seconds) | `120` |

Read by `auto/config` (`DISTRIBUTION`, `ARCHITECTURES`, `ARCHIVE_AREAS`,
`HOSTNAME`, `LOCALE`, `TIMEZONE`, mirrors; run `auto/config --help`):
`BOOTLOADERS` (default `syslinux,grub-efi`), `BOOTAPPEND` (default
`quiet splash`) and `VARIANT` (`installer`, `live` or `traditional`, default `installer`;
see [Image variants](#image-variants)). The live user is not an environment
variable: it is the `username:` in
`config/includes.chroot/etc/calamares/modules/removeuser.conf` (default
`user`), which `auto/config` and the installer-kiosk hook both read. (It used
to come from `USERNAME`, which desktop sessions set to the builder's own login
name, so local builds got a live user Calamares and the tty1 autologin did not
expect.)

Checksum verification is fail-closed: without `AURORA_STORE_SHA256` (and
without `LUXENOS_ALLOW_UNPINNED_DOWNLOADS=1`), the build stops rather than
shipping an unverified APK. Whether live-build forwards these host
variables into the chroot is not verified here; if the hook logs the defaults
instead of your values, set them in the environment of `sudo lb build`
(`sudo -E`).

## Build steps

```sh
sudo lb clean --purge
sudo lb config
sudo lb build
```

Produces `live-image-amd64.hybrid.iso` in the repo directory. A full build can
take 30-90 minutes. `auto/config` uses live-build 5 option names (Debian 13);
`lb config` from an older live-build will reject them.

### Image variants

`VARIANT` selects which session the image boots into by default:

| `VARIANT` | Boots into | Live session? | ISO volume label |
| --- | --- | --- | --- |
| `installer` (default) | The Calamares installer kiosk | yes | `LUXENOS` |
| `live` | The full Sway desktop | yes | `LUXENOS-LIVE` |
| `traditional` | The Debian installer (graphical/text) | **no** | `LUXENOS-INSTALL` |

```sh
sudo lb clean --purge && sudo lb config && sudo lb build                         # installer image
sudo lb clean --purge && sudo VARIANT=live lb config && sudo lb build            # live image
sudo lb clean --purge && sudo VARIANT=traditional lb config && sudo lb build     # traditional image
```

All three images contain the same packages and the same root filesystem.
`installer` and `live` differ only in the default session. The live image still has
Calamares, so you can install from it straight out of the launcher; the
installer image can be pushed to the desktop by adding `luxenos.session=desktop`
to the kernel command line (press `e` in the GRUB menu). The `traditional`
image also carries the Debian installer and its package pool (bootloader,
cryptsetup and firmware `.deb`s d-i installs from the ISO), so it is somewhat
larger; it has no live session to push anywhere.

Build one variant at a time: `lb config` writes into `config/`, so run
`lb clean --purge` between variants. The variant is recorded only in the ISO
volume label, so rename the output before building the second one if you want
to keep both.

### Image size

The image is built **without Debian's recommended packages**
(`--apt-recommends false` in `auto/config`) and without apt package lists
(`--apt-indices false`). Installing every Recommends made the root filesystem
~5.5 GiB; without them it is ~3.8 GiB. The recommended packages LuxenOS does
need (live user creation, Wi-Fi, microcode, common laptop firmware, audio
profiles, SVG icons, ...) are listed in
`config/package-lists/essentials.list.chroot`. The largest remaining items
are Wine (64- and 32-bit, ~1.3 GiB installed) and Firefox (~300 MiB).

The installer asks for **10 GiB** of disk (`requiredStorage` in
`config/includes.chroot/etc/calamares/modules/welcome.conf`, overriding
Debian's 15 GiB): the installed system, the Waydroid Android image downloaded
on first boot (~3 GiB), and room for updates. Each "Build LuxenOS ISO" run
prints a size breakdown; check it before adding packages.

## Testing with QEMU

```sh
sudo apt install qemu-system-x86 qemu-kvm
qemu-img create -f qcow2 luxenos-disk.qcow2 10G
qemu-system-x86_64 -m 4096 -enable-kvm -cdrom live-image-amd64.hybrid.iso \
    -drive file=luxenos-disk.qcow2,if=virtio
```

This boots the installer kiosk. Waydroid needs KVM (`-enable-kvm`) and
nested virtualization to run inside a VM; to look at the desktop instead,
add `luxenos.session=desktop` to the boot entry.

## Flashing to USB

```sh
lsblk
sudo dd if=live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Replace `/dev/sdX` with the whole USB device, not a partition. Booting from
the USB starts the installer: Calamares in a live session for the `installer`
image, the Debian installer (no live session) for the `traditional` image.
The `traditional` image is flashed the same way.

## First boot

After installing, log in through the greetd prompt and Sway starts. Waydroid
is already installed; internet access is required so its first-boot service
can download and initialize the Android image. To check initialization and
start the Android session (and install Aurora Store if the APK was bundled):

```sh
luxenos-setup-android
```

The first-boot service performs container initialization; this helper waits for it and
starts the session. It does not configure Google Play Store certification.

## Key bindings

| Shortcut | Action |
| --- | --- |
| Super+Return | Terminal (Foot) |
| Super+Space | Search: apps, actions, recent documents, files |
| Super+D | App launcher (wofi) |
| Super+Tab | Workspace overview |
| Super+N | Quick Settings |
| Super+Shift+N | Notification Center |
| Super+F1 | Assistant |
| Super+Shift+D | Keyboard focus to the dock (Esc returns) |
| Ctrl+Shift+Esc | System Monitor |
| Super+A | Open Waydroid / Android UI |
| Super+L | Lock screen |
| Super+Shift+E | Exit / logout |
| Super+F | Fullscreen |
| Super+1-4 | Switch workspaces |
| Print | Screenshot to clipboard + `~/Pictures` |
| Super+Shift+S | Select region + annotate (swappy) |
| Super+P | Cycle power profile |
| Super+C | Glass controls menu (network, sound, glass preset/theme, power) |
| Super+Shift+H | Run the health check |

## Glass, SwayFX and dependencies

### Is SwayFX active?

```sh
luxenos-glass status        # look at "compositor" and "compositor fx"
luxenos-glass apply --debug # shows every effect the compositor accepted/refused
swaymsg 'blur_radius 5'     # SwayFX: success. Stock Sway: "Unknown/invalid command"
```

The ISO build bundles **SwayFX 0.5.3** as `/usr/local/bin/sway`, built with
SceneFX 0.4.1 and wlroots 0.19.0 by `config/hooks/live/0200-build-swayfx.hook.chroot`.
The greetd compositor explicitly launches that binary; the Debian `sway`
package is retained as a fallback. The hook verifies the installed binary and
fails the ISO build if the SwayFX build is unsuccessful. Blur is the most
expensive effect on old GPUs: use
`luxenos-glass preset performance`, or `GLASS_EFFECTS=off`.

Layer effects (bar/launcher/notification blur) are matched by layer-shell
*namespace*, which Sway/SwayFX IPC cannot list. LuxenOS uses the values
observed from the shipped programs (`waybar`, `wofi`, `notifications`), only
for programs that are installed; override with `GLASS_NS_BAR`,
`GLASS_NS_LAUNCHER`, `GLASS_NS_NOTIFY` if you swap a program.

### Dependencies

| Component | Package(s) | Needed for | Kind |
| --- | --- | --- | --- |
| Sway, lock, idle | `sway` `swaylock` `swayidle` | the desktop | mandatory (graphical session) |
| Bar | `waybar` + `fonts-font-awesome` `fonts-inter` `fonts-noto` | glass bar | mandatory (graphical session) |
| Launcher | `wofi` | Super+D, Controls menu | mandatory (graphical session) |
| Notifications | `dunst` `libnotify-bin` | glass notifications | mandatory (graphical session) |
| Privilege prompts | `polkitd` `pkexec` `polkit-kde-agent-1` | .deb installs, updates | mandatory (graphical session) |
| Dialogs | `zenity` (GTK4/libadwaita) | .deb/.exe helpers | mandatory (graphical session) |
| GTK3 / GTK4 apps | `thunar`, `zenity`, `adwaita-icon-theme`, `dconf-gsettings-backend`, `dconf-service` | accents, dark/light | mandatory (graphical session) |
| Session plumbing | `dbus-user-session` `dbus-bin` `procps` | env export, single-instance checks | mandatory |
| Network | `network-manager` (base) + `network-manager-gnome` (`nm-applet`, `nm-connection-editor`) | Wi-Fi/Ethernet UI and password prompts | mandatory for the network tray; without it `luxenos-network` falls back to `nmtui` |
| GTK theme tools | `nwg-look`, `swappy`, `grim`, `slurp` | Controls menu, screenshots | optional |
| SwayFX | built from pinned upstream sources by the ISO hook | blur, rounded windows, shadows | bundled |
| LuxenOS apps | `python3-gi` `python3-gi-cairo` `gir1.2-gtk-4.0` `gir1.2-gtk4layershell-1.0` `python3-apt` | launcher, dock, Quick Settings, overview, Software Center, System Monitor, Restore Center, welcome | mandatory (checked by hook 0400) |
| Night light, file search | `wlsunset`, `plocate` | Quick Settings, launcher | optional (shown as unavailable / folder scan) |
| Hardware Manager | `pciutils` `usbutils` `mokutil` (+ `fwupd`) | device lists, Secure Boot state | in `luxenos-system.list.chroot`; missing tools give empty sections |
| Software Center apps | `appstream` `gir1.2-appstream-1.0` | app names, descriptions, screenshots | optional at run time (falls back to package names) |
| Notification sounds | `sound-theme-freedesktop` (+ `pipewire-bin`'s `pw-play`) | sounds | optional |
| Plugin sandbox | `bubblewrap` | launcher plugins | without it no plugin runs |
| Phone Connect | `kdeconnect` | phone integration | **not in the image**; installed from Phone Connect on request |
| Login screen on Debian 14 | `gtkgreet` | greeter where wlgreet is missing | forky builds only |
| Files extras | `thunar-archive-plugin` `xarchiver` `thunar-volman` `ffmpegthumbnailer` | archives, USB auto-mount, video thumbnails | optional |

`nm-applet` is started once by `luxenos-session-init`, only if
NetworkManager is running. The Waybar network module only shows status; its
click opens the connection editor (`luxenos-network`). It is not a second
applet.

## Configuration

Every new feature is configured with KEY=VALUE files (parsed, never
executed), per user under `~/.config/luxenos/` unless noted:

| File | What | Written by |
| --- | --- | --- |
| `/etc/luxenos/glass.conf`, `glass.conf` | look; new keys `GLASS_ICON_THEME`, `GLASS_CURSOR_THEME`, `GLASS_CURSOR_SIZE`, `GLASS_BAR_POSITION` | Personalization, `luxenos-glass` |
| `wallpaper.conf` | `WALLPAPER`, `WALLPAPER_MODE`, `WALLPAPER_OUTPUT_<NAME>`, `WALLPAPER_WS_<N>`, `SLIDESHOW*` | Personalization, `luxenos-wallpaper`, theme packs |
| `search.conf` | `SEARCH_HISTORY`, `SEARCH_HISTORY_SIZE`, `SEARCH_WEB`, `SEARCH_WEB_DEFAULT`, `SEARCH_WEB_ENGINE_<ID>`, `SEARCH_PLUGINS` | by hand |
| `widgets.conf` | `WIDGETS`, `WIDGET_<ID>_POS`, `WEATHER_LAT/LON/PLACE/UNITS/MINUTES/URL` | Desktop widgets settings |
| `notifications.conf` | `NOTIFY_SOUND`, `NOTIFY_SOUND_FILE`, `NOTIFY_HISTORY`, `APP_<KEY>_MODE/SOUND/URGENCY` | Notification Center (generates `~/.config/dunst/dunstrc.d/50-luxenos-notifications.conf`) |
| `overview.conf` | `OVERVIEW_PREVIEWS=yes|no` | by hand |
| `plugins.conf` | `PLUGIN_<ID>=yes|no` | Plugins |
| `assistant.conf` | `ASSISTANT_BACKEND=offline|ollama`, `ASSISTANT_URL` (loopback only), `ASSISTANT_MODEL` | by hand |
| `wine-apps.list` | `PATH<TAB>PREFIX` per Windows program | Windows Programs |
| `/etc/luxenos/updater.conf` | existing keys plus `SNAPSHOT_BEFORE_UPDATE`, `SNAPSHOT_RETENTION`, `SNAPSHOT_AUTO_PRUNE` | Update Manager (via `luxenos-pkg-root set-update-schedule`) |

New commands: `luxenos-update-manager [--notify]`, `luxenos-hardware [--report]`,
`luxenos-secureboot status|check`, `luxenos-wallpaper …`, `luxenos-widgets
[--settings|--restart|--stop]`, `luxenos-notifications [--write-config]`,
`luxenos-personalize [--page …]`, `luxenos-android`, `luxenos-phone`,
`luxenos-wine-manager`, `luxenos-wine-open --prefix NAME FILE`,
`luxenos-plugins`, `luxenos-assistant`, `luxenos-theme install FILE`.
New root helpers (pkexec, administrator password): `luxenos-snapshot-root`
(polkit `org.luxenos.snapshot`), `luxenos-android-root` (`org.luxenos.android`),
`luxenos-firewall-root` (`org.luxenos.firewall`); `luxenos-pkg-root` gained
`download-upgrades`, `security-upgrade`, `flatpak-update`, `firmware-update`,
`waydroid-upgrade`, `set-update-schedule`.

### Writing a launcher plugin

A folder in `~/.local/share/luxenos/plugins/<id>/` with `plugin.conf`
(`PLUGIN_ID`, `PLUGIN_NAME`, `PLUGIN_VERSION`, `PLUGIN_API=1`,
`PLUGIN_TYPE=search`, `PLUGIN_EXEC`, optional `PLUGIN_PERMISSIONS=network
home-read`) and an executable that gets the query as its argument and prints
`{"results": [{"title": "…", "subtitle": "…", "copy": "…", "open": "https://…"}]}`.
It runs in a bubblewrap sandbox (read-only `/usr`, no network or home unless
declared) with a time limit; enable it in Plugins. See the shipped
`/usr/share/luxenos/plugins/unicode-chars/` ("char arrow", "emoji heart").

## Debian 14 (forky)

Status checked on 2026-11-01 against debian.org and the archive
(api.ftp-master.debian.org): Debian 13 "trixie" is stable (13.7); Debian 14
"forky" is **testing, with no release date and no freeze announced**. LuxenOS
therefore stays on trixie. A forky image can be built for development:

```sh
sudo LUXENOS_EXPERIMENTAL_FORKY=1 DISTRIBUTION=forky lb config && sudo lb build
```

The package lists carry live-build conditionals (`#if DISTRIBUTION …`),
evaluated by live-build and by `tools/expand-package-lists` (CI resolves both
sets; the forky job is informational). Differences found in the archive:

| trixie | forky (testing, today) | LuxenOS handling |
| --- | --- | --- |
| `wine`, `wine64`, `libwine`, `fonts-wine`, `wine32` | only in unstable | excluded on forky; hook 0250 builds without Wine there and `.exe` files report "Wine is not installed" |
| `wlgreet` | only in unstable | forky uses `gtkgreet` (`luxenos-greeter` falls back to it, `/usr/share/luxenos/ui/gtkgreet.css`) |
| `evince` | only in unstable | forky uses `papers` |
| `apparmor-profiles-extra` | removed | trixie only |
| `network-manager-gnome` (transitional) | removed | both use `network-manager-applet` + `nm-connection-editor` |

Versions in forky at that date, for reference only (they will change): sway
1.12, waybar 0.15, GTK 4.24, gtk4-layer-shell 1.3, Python 3.14, systemd 262,
PipeWire 1.6.9, WirePlumber 0.5.18, Linux 7.2, GRUB 2.14, firmware 20260916.
Not verified on forky: the SwayFX build hook (pinned wlroots 0.19 / SceneFX 0.4
from source), Calamares 3.4, Waydroid's own repository for forky, and any boot.
Nothing about forky has been built or booted here.

## Secure Boot

`luxenos-secureboot status|check` and the Hardware Manager report the
firmware mode, the Secure Boot and Setup Mode state, and whether each link of
Debian's signed chain is installed: `shim-signed` (Microsoft-signed shim),
`grub-efi-amd64-signed` (Debian-signed GRUB), a signed Debian kernel
(`linux-signed-*`), and with `sudo` the files on the EFI system partition.
LuxenOS never enables or disables Secure Boot and never writes firmware
variables or keys.

Building with `LUXENOS_SECURE_BOOT=1` passes `--uefi-secure-boot enable` to
live-build and adds `shim-signed` and `grub-efi-amd64-signed` to the image,
so Calamares' `grub-install` sets up the signed chain on the installed
system. **This has not been tested on real firmware**, so LuxenOS does not
claim Secure Boot support. SwayFX and Waydroid are user-space and unaffected;
out-of-tree kernel modules (DKMS) need a Machine Owner Key:

```sh
sudo mokutil --import /var/lib/dkms/mok.pub   # choose a one-time password
# reboot: the blue MokManager screen asks to "Enroll MOK" with that password
```

Recovery: if a Secure Boot machine no longer boots LuxenOS, turn Secure
Boot off in the firmware settings (nothing on the disk has to change), boot,
and run `sudo luxenos-secureboot check` to see which link is missing.

## Troubleshooting

**Update Manager says another package operation is running.** The nightly
updater, the Software Center and the Update Manager share one lock
(`/run/luxenos-pkg.lock`); wait for the other one. `pgrep -a apt` shows it.

**No snapshots page / rollback not offered.** Snapshots need Btrfs; rollback
also needs `subvol=` in fstab, `/boot` inside the system subvolume and a
separate `/home`. `/usr/libexec/luxenos/luxenos-snapshot-root status` prints
why. After a rollback the old system is kept as `@.before-rollback-DATE`
(delete it from a rescue system when you no longer need it).

**Phone not found.** Allow phone connections in Phone Connect (it opens ports
1714-1764 only for your local network), both devices on the same network.

**Weather widget says "Set a location".** Nothing is sent anywhere until you
set a place in the widget settings.


**The greetd login appears, but Sway does not start after authentication.**
Check `systemctl status greetd` and `journalctl -u greetd -b`. Verify
`/etc/greetd/config.toml` launches `/usr/local/bin/sway --config /etc/greetd/sway-config`,
that `wlgreet` is installed, and ensure Sway and its
Wayland dependencies are installed. If Sway exits immediately, try running
`sway` from a separate TTY and inspect `journalctl -b` for GPU/Mesa errors.

**No blur / rounded windows.**
Check `luxenos-glass status` for `compositor fx yes` and run
`luxenos-glass apply --debug`. The ISO build should fail if SwayFX did not
compile; if this appears after installation, check whether `/usr/local/bin/sway`
is present and inspect the build log.

**Bar, launcher or notifications look unstyled.**
```sh
luxenos-glass apply --debug
ls ~/.config/luxenos/glass/          # waybar.css wofi.css gtk3.css ... should exist
```
Hand-edited files lose their `luxenos-glass: managed` marker and are then left
alone, so delete `~/.config/wofi/style.css` or `~/.config/dunst/dunstrc` to get
the generated version back. Dunst without `dunstctl reload` picks up theme
changes at the next restart (`pkill dunst; dunst &`).

**No network icon.**
```sh
systemctl is-active NetworkManager   # must be "active"
pgrep -a nm-applet                   # one process
luxenos-network                      # opens the editor (or nmtui)
```

**Graphical password prompts (.deb install) never appear.**
`pgrep -af polkit` should show an agent; start one with
`luxenos-polkit-agent &`.

**`waydroid-first-init.service` failed on first boot.**
```sh
systemctl status waydroid-first-init.service
journalctl -u waydroid-first-init.service
sudo systemctl restart waydroid-first-init.service
```
Usually a flaky network during the Android image download. Running the 
command above will retry the initialization.

**Waydroid never starts.**
Check CPU virtualization is available: `grep -E 'vmx|svm' /proc/cpuinfo`
(also checked by `luxenos-health-check`). Enable virtualization/nested
virtualization in BIOS/hypervisor settings if it's missing.

**Screen seems frozen or unresponsive.**
On some laptops with low RAM, try running `swaymsg exit` from a terminal 
(or SSH in) and restarting Sway. If issues persist, check available memory 
and swap with `free -h`.

## Known limitations

- **Not exercised in a real build/install here:** the full ISO build, the
  Calamares live-to-installed conversion (removeuser, purging live-boot,
  initramfs rebuild), the `traditional` image's Debian-installer install
  (live-installer copy, preseed, `luxenos-finish-install` late command),
  real SwayFX rendering, and Wine's first-run prefix
  creation. The scripts and configs behind them were tested in isolation; see
  the verification report that accompanies each change.
- **LuxenOS desktop apps:** tested by unit tests and by starting every app
  in a headless Sway (CI job `desktop-apps`, screenshots uploaded), not on
  real hardware: Wi-Fi/Bluetooth/audio/backlight/battery controls, polkit
  prompts, pointer drag-and-drop in the overview, dock auto-hide and
  multi-monitor hot-plug have not been exercised with real devices. The
  Software Center: AppStream names, descriptions and screenshots and the
  Flatpak page were not exercised here (no AppStream bindings, no Flatpak on
  the test machine); there are no ratings or reviews. Thunar runs
  files that already have the executable permission when double-clicked
  (scripts ask first); downloads are not executable unless you make them so.
- Secure Boot (`LUXENOS_SECURE_BOOT=1`) is untested on real firmware.
- **New in this release, tested only by unit tests and a headless GTK start
  (Broadway backend, no compositor):** Update Manager, Hardware Manager,
  Notification Center, Personalization, widgets, Android, Phone Connect,
  Windows Programs, Plugins, Assistant. Not exercised: real Sway/SwayFX
  layer-shell placement of widgets and notifications, overview previews
  (grim), Waydroid commands, KDE Connect pairing, Flatpak installs, Wine prefix
  creation, polkit prompts of the new helpers, Btrfs snapshot creation and
  rollback (the end-to-end test needs root and loop devices and was skipped),
  dunst rule reloading.
- Phone Connect depends on KDE Connect (Qt/KDE libraries, installed on
  request); automatic clipboard sync from the computer is limited on Wayland.
- Window previews only exist for windows that are on screen when the
  overview opens (Sway does not render hidden workspaces).
- The image is built without Debian's *recommended* packages (see "Image
  size"). Hardware whose firmware is not in
  `config/package-lists/essentials.list.chroot` (for example NVIDIA or
  MediaTek Wi-Fi) needs its `firmware-*` package added there, and CJK text
  needs `fonts-noto-cjk`.

## Project layout

```text
auto/config                                            live-build configuration (VARIANT=installer|live|traditional)
config/package-lists/desktop.list.chroot                Sway, Waybar, Firefox, desktop apps
config/package-lists/android.list.chroot               Waydroid prerequisites (Waydroid itself comes from hook 0100)
config/hooks/live/0100-install-waydroid.hook.chroot    Waydroid + Aurora Store + first-boot service
config/hooks/live/0150-installer-kiosk.hook.chroot     Live-session tty1 autologin (installer kiosk)
config/hooks/live/0200-configure-premium-defaults.hook.chroot
                                                        Services, firewall, Flathub, Plymouth theming
config/includes.chroot/etc/skel/.config/                Default Sway/Waybar/wofi configuration
config/includes.chroot/etc/sysctl.d/99-luxenos.conf    Kernel hardening settings
config/includes.chroot/usr/local/bin/                  Helper scripts (luxenos-glass, luxenos-session-init, luxenos-deb-install, luxenos-wine-open, luxenos-setup-android, ...)
config/includes.chroot/etc/profile.d/luxenos-session.sh Starts Sway on tty1 (installer kiosk in the live session)
config/includes.chroot/etc/calamares/                  Installer sequence, branding, post-install cleanup
config/includes.chroot/usr/libexec/luxenos/            Privileged package helper (pkexec only), luxenos-finish-install (post-install conversion, both installers)
config/includes.installer/preseed.cfg                  Debian installer answers + late_command (traditional image)
config/hooks/live/0900-traditional-boot-menu.hook.binary
                                                        Removes the live boot entries from the traditional image
config/includes.chroot/etc/luxenos/                    glass.conf, wine.conf, updater.conf
config/package-lists/essentials.list.chroot            Recommended packages LuxenOS needs (the build skips Recommends)
config/package-lists/wine.list.chroot                  Debian Wine packages (32-bit half via hook 0250)
config/hooks/live/0250-install-wine-and-integrations.hook.chroot
                                                        i386 multiarch + wine32, MIME/desktop DBs, build-time verification
config/package-lists/luxenos-system.list.chroot       Runtime of the system-management apps (lspci, AppStream, sounds, bubblewrap)
config/includes.chroot/usr/libexec/luxenos/            + luxenos-snapshot-root, luxenos-android-root, luxenos-firewall-root
config/includes.chroot/usr/share/luxenos/plugins/      Shipped launcher plugins
tools/expand-package-lists                              Package set per Debian release (live-build conditionals)
.github/workflows/validate.yml                          CI: tree validation + lb config dry-run
tools/validate-release-tree                             Local/CI structural + syntax checks
CONTRIBUTING.md                                         Development guide
README.md                                               This file
```

## License

GPLv3 — see [LICENSE](LICENSE).
