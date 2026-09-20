# LuxenOS

A free, open-source Linux desktop for x86_64 laptops that runs real Android
apps natively (Waydroid), with a translucent "liquid glass" Sway desktop.
You boot a USB stick, install with Calamares, and log in to Sway.

## What this is (and isn't)

LuxenOS is a **Debian remix**, built with `live-build`: Debian 13 "Trixie"
underneath, with a curated Sway/Wayland desktop, Waydroid for Android apps,
and a graphical installer. It is not a kernel or userland written from zero -
no serious general-purpose desktop OS is, realistically, without a multi-year
team effort. What *is* built here, specifically for this project: the desktop
configuration and glass theme, the Android integration and its first-boot
setup flow, the installer session, the branding and boot behavior, and the
hardening layered on top of Debian.

The build produces one **live/installer hybrid ISO** (`live-build`
`iso-hybrid`). See [How LuxenOS boots](#how-luxenos-boots) for exactly what
happens in the live session, in the installer, and on the installed system.

## Core functions

### Android app compatibility
[Waydroid](https://waydroid.org/) runs a real Android container alongside
your Linux desktop; Android apps appear as regular windows you can resize,
snap, and switch between like anything else. Set up by
`config/hooks/live/0100-install-waydroid.hook.chroot`:
- Downloads and configures the Waydroid repo/package at build time.
- Ships Aurora Store (an open-source Play Store frontend — no Google account
  needed) pre-downloaded and checksum-verified, so it's ready to install
  from first boot.
- Creates a one-shot systemd service that initializes the Android image on
  the *installed* system's first real boot.
- `luxenos-setup-android` (installed to `/usr/local/bin`; also in the app
  launcher as "Android Setup") walks through
  Aurora Store installation and, optionally, official Google Play Store
  certification for apps that require a Google account.

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

| | Stock Sway (what the ISO ships) | SwayFX (optional) |
| --- | --- | --- |
| Translucent panels, borders, accent, sheen | yes | yes |
| Rounded panels (Waybar/wofi/dunst/GTK) | yes (CSS) | yes |
| Blur behind panels | no (opacity is raised to stay readable) | yes |
| Rounded *window* corners, window shadows | no | yes |
| Layer effects (bar/launcher/notification blur + shadows) | no | yes |
| Dimmed inactive windows | no | yes (`rich`) |

Nothing is assumed: `luxenos-glass` asks the *running* compositor whether it
accepts SwayFX-only commands, and applies every setting independently, so one
unsupported effect never stops the rest. Text is never left unreadable:
panel opacity is raised until text keeps the configured contrast
(`GLASS_MIN_CONTRAST`) over the worst-case backdrop, whatever the preset.

**Presets** (`GLASS_PRESET`, or `luxenos-glass preset ...`; Super+C menu):

| Preset | Blur | Shadows | Panel opacity | Animation | Extras |
| --- | --- | --- | --- | --- | --- |
| `performance` | off | off | 88% | none | radius 8, gaps 4 - for old GPUs (e.g. Dell Latitude E6440) |
| `balanced` (default) | radius 5, 2 passes | soft | 68% | 120 ms | radius 12, gaps 6 |
| `rich` | radius 8, 3 passes | deep | 55% | 200 ms | radius 16, punchier blur colour, inactive windows dimmed |
| `flat` (command only) | off | off | 100% | none | no glass at all |

**Configuration.** System defaults: `/etc/luxenos/glass.conf` (documented,
every key explained). Your overrides: `~/.config/luxenos/glass.conf`, same
format, wins over the system file. The file is parsed, not sourced; invalid
values are reported and replaced by safe defaults. Missing files are fine.

**Accessibility.** `GLASS_REDUCE_MOTION=yes` sets all transitions to zero
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

### Installation
Calamares, with LuxenOS branding
(`config/includes.chroot/etc/calamares/branding/luxenos/`) and support for
full-disk LUKS encryption. The live session boots straight into it; see below.

### Hardening
AppArmor with real profile packages (not just the empty service), UFW
default-deny-incoming, sysctl hardening
(`config/includes.chroot/etc/sysctl.d/99-luxenos.conf`), and
`unattended-upgrades` enabled on the installed system.

## How LuxenOS boots

The ISO is a standard `live-build` **live/installer hybrid** (`iso-hybrid`,
BIOS via syslinux and UEFI via GRUB). It is *not* a pre-installed disk image:
the installed system exists only after Calamares has copied it to disk.

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
Launched by the kiosk session. Its sequence
(`config/includes.chroot/etc/calamares/settings.conf`) partitions the disk,
unpacks this same root filesystem (`unpackfs`), creates your account
(`users`), installs the bootloader, then converts the copy into a normal
system: `removeuser` deletes the live account and a `shellprocess` job purges
`live-boot`/`live-config`, rebuilds the initramfs, and deletes the kiosk
files (`shellprocess-luxenos-cleanup.conf`). There is no display manager
job because LuxenOS has no display manager.

**3. Installed system.**
No `boot=live`, no kiosk. You log in at the tty1 console;
`/etc/profile.d/luxenos-session.sh` starts Sway with your
`~/.config/sway/config` (copied from `/etc/skel`), which runs
`luxenos-glass`, Waybar, `luxenos-session-init` (polkit agent, dunst,
nm-applet), and the rest. Opt out of the autostart with
`touch ~/.config/luxenos/no-autostart`. Hardening (AppArmor, UFW,
unattended-upgrades) and the Waydroid first-boot service belong to the
installed system.

The installer-to-installed conversion (steps 2-3) has not been exercised in a
real install yet; see "Known limitations".

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
- Boot: BIOS (syslinux) and UEFI (`grub-efi`), no Secure Boot

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
| `AURORA_STORE_URL` | URL to download the Aurora Store APK from | unset = Aurora Store is not bundled |
| `AURORA_STORE_DIR` | Directory for the downloaded APK | `/usr/share/luxenos/apks` |
| `AURORA_STORE_SHA256` | Expected SHA-256 of the APK | **Required for release builds** when `AURORA_STORE_URL` is set |
| `LUXENOS_ALLOW_UNPINNED_DOWNLOADS` | `1` to build without `AURORA_STORE_SHA256` | Dev builds only |
| `DOWNLOAD_RETRIES` | Retry count for network downloads | `3` |
| `CURL_TIMEOUT` | Per-attempt curl timeout (seconds) | `120` |

Read by `auto/config` (`DISTRIBUTION`, `ARCHITECTURES`, `ARCHIVE_AREAS`,
`HOSTNAME`, `USERNAME`, `LOCALE`, `TIMEZONE`, mirrors; run `auto/config --help`):
`BOOTLOADERS` (default `syslinux,grub-efi`) and `BOOTAPPEND` (default
`quiet splash`). `USERNAME` is the live user and must match
`etc/calamares/modules/removeuser.conf`.

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

Produces `live-image-amd64.hybrid.iso` in the repo directory. Boot it to run the
installer (see [How LuxenOS boots](#how-luxenos-boots)). A full build can take
30-90 minutes. `auto/config` uses live-build 5 option names (Debian 13);
`lb config` from an older live-build will reject them.

## Testing with QEMU

```sh
sudo apt install qemu-system-x86 qemu-kvm
qemu-system-x86_64 -m 4096 -enable-kvm -cdrom live-image-amd64.hybrid.iso
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
the USB starts the installer.

## First boot

After installing, log in on tty1 and Sway starts. Waydroid is already
installed; internet access is required so it can download and initialize its
Android image. To complete Android setup and install Aurora Store:

```sh
luxenos-setup-android
```

This will initialize the Android container and optionally configure Google Play Store access.

## Key bindings

| Shortcut | Action |
| --- | --- |
| Super+Return | Terminal (Foot) |
| Super+D | App launcher (wofi) |
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

The ISO ships **stock Debian Sway**. SwayFX is an optional drop-in replacement
for the `sway` binary that is not in the Debian archive; LuxenOS does not
build or bundle it. If you install one, it must be built against the wlroots
it names (SwayFX's current `main` requires wlroots 0.20, so pick a release
matching the wlroots your Debian ships). Log out and back in; no LuxenOS
file changes. Blur is the most expensive effect on old GPUs: use
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
| SwayFX | not in Debian | blur, rounded windows, shadows | optional, SwayFX-only |

`nm-applet` is started once by `luxenos-session-init`, only if
NetworkManager is running. The Waybar network module only shows status; its
click opens the connection editor (`luxenos-network`). It is not a second
applet.

## Troubleshooting

**Boots to a text login instead of Sway.**
Log in on tty1 (Sway starts from `/etc/profile.d/luxenos-session.sh`, only on
tty1, only as a normal user). Check `~/.config/luxenos/no-autostart` does not
exist, then run `sway` by hand to see errors, and `journalctl -b` for the
session. Ensure `libgl1`/Mesa drivers support your GPU.

**No blur / rounded windows.**
Expected on stock Sway. `luxenos-glass status` shows `compositor sway` and
`compositor fx no`; see "Is SwayFX active?" above.

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
  initramfs rebuild), real SwayFX rendering, and Wine's first-run prefix
  creation. The scripts and configs behind them were tested in isolation; see
  the verification report that accompanies each change.
- No Secure Boot / shim.
- `libwayland-dev` and `libinput-dev` in `desktop.list.chroot` are
  development packages that a runtime desktop does not need; they are kept
  only because removing them was out of scope for the glass work.

## Project layout

```text
auto/config                                            live-build configuration (live/installer hybrid ISO)
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
config/includes.chroot/usr/libexec/luxenos/            Privileged package helper (pkexec only)
config/includes.chroot/etc/luxenos/                    glass.conf, wine.conf, updater.conf
config/package-lists/wine.list.chroot                  Debian Wine packages (32-bit half via hook 0250)
config/hooks/live/0250-install-wine-and-integrations.hook.chroot
                                                        i386 multiarch + wine32, MIME/desktop DBs, build-time verification
.github/workflows/validate.yml                          CI: tree validation + lb config dry-run
tools/validate-release-tree                             Local/CI structural + syntax checks
CONTRIBUTING.md                                         Development guide
README.md                                               This file
```

## License

GPLv3 — see [LICENSE](LICENSE).
