# LuxenOS

A free, open-source Linux desktop for x86_64 laptops that runs real Android
apps natively. Boot the ISO and you're ready to use the system immediately —
no installation step required.

## What this is (and isn't)

LuxenOS is a **Debian remix**, built with `live-build`: Debian 13 "Trixie"
underneath, with a curated Sway/Wayland desktop, Waydroid for Android apps,
and a complete pre-installed filesystem, all assembled into one image. It is not a
kernel or userland written from zero — no serious general-purpose desktop OS
is, realistically, without a multi-year team effort. What *is* built here,
specifically for this project: the desktop environment configuration, the
Android integration and its first-boot setup flow, the branding and boot behavior,
and the hardening/theming layered on top of Debian.

The ISO this repo builds contains a complete, ready-to-use Debian installation. 
Boot it on any compatible x86_64 laptop and you'll have a fully functional 
Sway desktop with Waydroid configured and ready for Android apps.

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
- `lapdroid-setup-android` (installed to `/usr/local/bin`) walks through
  Aurora Store installation and, optionally, official Google Play Store
  certification for apps that require a Google account.

### The desktop
Sway (tiling Wayland compositor) + Waybar, configured under
`config/includes.chroot/etc/skel/.config/`, so every new user account gets
it by default. Includes screenshot/annotation (`grim`/`slurp`/`swappy`), a
power-profile switcher tied to `tlp`, GTK theming via `nwg-look`, and a
consistent color palette across Sway/Waybar/wofi/GRUB/Plymouth.

### Installation
Calamares, with LuxenOS branding
(`config/includes.chroot/etc/calamares/branding/luxenos/`) and support for
full-disk LUKS encryption. See below for how the boot flow works.

### Hardening
AppArmor with real profile packages (not just the empty service), UFW
default-deny-incoming, sysctl hardening
(`config/includes.chroot/etc/sysctl.d/99-luxenos.conf`), and
`unattended-upgrades` enabled on the installed system.

## Pre-installed ISO: what it means

This is a **non-live, pre-installed ISO**. Unlike a live ISO (Ubuntu, Fedora 
LiveUSB) that boots into a demo desktop with an "Install" button, LuxenOS is 
a complete installed filesystem shipped as an ISO. Boot it and you're in the 
Sway desktop immediately — no installation wizard.

**Why this approach?**

- **Single purpose.** No confusion between a temporary live session and your 
  actual system.
- **Better on older hardware.** Live overlays and squashfs decompression add 
  overhead; a pre-installed filesystem is leaner at runtime.
- **Simpler mental model.** You know exactly what you're getting.

Built on `live-build` configured in "debian" mode (installed filesystem) rather 
than "live" mode. The ISO is hybrid (boots from USB or CD), and what boots from 
it is what you use — no separate install step.

**Tradeoff:** You can't explore the desktop without committing. If you want to 
try before installing, you can fork this repo and enable live-boot mode in 
`auto/config` — that's a few-minute change.

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
- Desktop (post-install): Sway + Waybar
- Android runtime: Waydroid, GAPPS or VANILLA image, Aurora Store fallback
- Boot mode: UEFI via `grub-efi-amd64`

## Requirements to build

```sh
sudo apt update
sudo apt install live-build curl ripgrep
```

## Environment variables

Required by `config/hooks/live/0100-install-waydroid.hook.chroot`:

| Variable | Purpose | Notes |
| --- | --- | --- |
| `WAYDROID_VERSION` | Version string, logged only | Informational |
| `WAYDROID_IMAGE_TYPE` | `GAPPS` or `VANILLA` | Required; build fails on any other value |
| `WAYDROID_REPO_URL` | URL of the Waydroid repo-setup script | Required |
| `WAYDROID_INSTALL_SCRIPT` | Local path to save that script to | Required |
| `AURORA_STORE_URL` | URL to download the Aurora Store APK from | Required to bundle Aurora Store |
| `AURORA_STORE_DIR` | Local directory for the downloaded APK | Required to bundle Aurora Store |
| `AURORA_STORE_SHA256` | Expected SHA-256 of the APK | **Required for release builds** |
| `LUXENOS_ALLOW_UNPINNED_DOWNLOADS` | `1` to build without `AURORA_STORE_SHA256` | Dev builds only |
| `DOWNLOAD_RETRIES` | Retry count for network downloads | Required |
| `CURL_TIMEOUT` | Per-attempt curl timeout (seconds) | Required |

Checksum verification is fail-closed: without `AURORA_STORE_SHA256` (and
without `LUXENOS_ALLOW_UNPINNED_DOWNLOADS=1`), the build stops rather than
shipping an unverified APK.

## Build steps

```sh
sudo lb clean --purge
sudo lb config
sudo lb build
```

Produces `debian-live-13-amd64-hybrid.iso`. This is a complete, pre-installed 
system. Boot it and you're in the Sway desktop immediately. A full build can 
take 30–90 minutes.

## Testing with QEMU

```sh
sudo apt install qemu-system-x86 qemu-kvm
qemu-system-x86_64 -m 4096 -enable-kvm -cdrom debian-live-13-amd64-hybrid.iso
```

This will boot straight into the Sway desktop with Waydroid pre-configured.

## Flashing to USB

```sh
lsblk
sudo dd if=debian-live-13-amd64-hybrid.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

Replace `/dev/sdX` with the whole USB device, not a partition. Boot from the 
USB and you'll be in the Sway desktop immediately.

## First boot

On first boot, you'll be in the Sway desktop with Waydroid already installed. 
Internet access is required so Waydroid can download and initialize its 
Android image. To complete Android setup and install Aurora Store:

```sh
lapdroid-setup-android
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
| Super+Shift+H | Run the health check |

## Troubleshooting

**Boots to a black screen/terminal instead of Sway.**
Check the systemd journal: `journalctl -b`. Usually a display server or 
configuration issue. Ensure `libgl1` and Mesa drivers are installed and 
your GPU is supported.

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

## Project layout

```text
auto/config                                            live-build configuration (debian mode, installed filesystem)
config/package-lists/desktop.list.chroot                Sway, Waybar, Firefox, desktop apps
config/package-lists/waydroid-deps.list.chroot          Waydroid + container dependencies
config/hooks/live/0100-install-waydroid.hook.chroot    Waydroid + Aurora Store + first-boot service
config/hooks/live/0200-configure-premium-defaults.hook.chroot
                                                        Services, firewall, Flathub, Plymouth theming
config/includes.chroot/etc/skel/.config/                Default Sway/Waybar/wofi configuration
config/includes.chroot/etc/sysctl.d/99-luxenos.conf    Kernel hardening settings
config/includes.chroot/usr/local/bin/                  Helper scripts (luxenos-health-check, lapdroid-setup-android)
.github/workflows/validate.yml                          CI: tree validation + lb config dry-run
tools/validate-release-tree                             Local/CI structural + syntax checks
CONTRIBUTING.md                                         Development guide
README.md                                               This file
```

## License

GPLv3 — see [LICENSE](LICENSE).
