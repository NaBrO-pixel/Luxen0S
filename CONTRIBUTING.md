# Contributing to LuxenOS

This project is a `live-build` tree. Most contributions are adding or
editing files in one of a few well-defined places.

## Where things go

| You want to... | Put it in... |
| --- | --- |
| Add/remove a package installed on the image | `config/package-lists/*.list.chroot` (Recommends are not installed: list what you need, see `essentials.list.chroot`) |
| Change build-time settings (distro, arch, bootloader) | `auto/config` |
| Run a script during the chroot build | `config/hooks/live/NNNN-description.hook.chroot` (numbered so ordering is explicit) |
| Ship a file verbatim into the built filesystem | `config/includes.chroot/<absolute path in the final system>` |
| Change the *installed* system's default desktop config | `config/includes.chroot/etc/skel/.config/...` |
| Change the *live boot/installer* kiosk session | `config/includes.chroot/etc/luxenos-installer/` and `config/hooks/live/0150-installer-kiosk.hook.chroot` |
| Add a CLI helper for users | `config/includes.chroot/usr/local/bin/` |
| Add a package only one Debian release has | wrap it in `#if DISTRIBUTION trixie` / `#endif` in the list (live-build syntax, no nesting, no `!` lines); check with `tools/expand-package-lists trixie` and `forky` |
| Add a desktop app | module in `usr/lib/luxenos/python/luxenos/`, a 10-line launcher in `usr/local/bin/`, a `.desktop` file, then list it in hook 0400 (`APPS`, imports, desktop-file check), `tools/validate-release-tree` and `tools/gui-smoke-test` |
| Add something that needs root | a small helper in `usr/libexec/luxenos/` with a fixed list of subcommands and validated arguments, a polkit action pointing at it in `org.luxenos.pkg.policy`, and a check in the validator; never make an app run as root |
| Add a launcher plugin | `usr/share/luxenos/plugins/<id>/` (`plugin.conf` + executable, see README "Writing a launcher plugin") |

## Two boot experiences, one repo

LuxenOS's live/boot session and its installed end-state are deliberately
different, and it trips people up the first time:

- **Live/boot session** (what you see running from USB): a minimal Sway
  kiosk that launches straight into Calamares. Config for this lives under
  `config/includes.chroot/etc/luxenos-installer/`.
- **Installed system** (what you get after Calamares finishes): the full
  Sway + Waybar desktop with Waydroid, defined under
  `config/includes.chroot/etc/skel/.config/`.

`config/includes.chroot/etc/calamares/modules/removefiles.conf` is what
strips the kiosk-only pieces from the installed disk, so double-check that
file if you add new kiosk-only files that shouldn't survive into the
installed system.

## Hook numbering

Hooks in `config/hooks/live/` run in filename order:

- `0100-install-waydroid.hook.chroot` — installs Waydroid, downloads Aurora
  Store, sets up first-boot Android initialization. This is the core
  Android-app-compatibility feature; changes here should be tested with an
  actual `lb build` + boot, not just `sh -n`.
- `0150-installer-kiosk.hook.chroot` — configures the live/boot session to
  autologin straight into Calamares.
- `0200-configure-premium-defaults.hook.chroot` — enables services, firewall
  defaults, Plymouth/GRUB theming, unattended-upgrades.

## Before opening a PR

```sh
./tools/validate-release-tree
```

This checks required files exist, hooks/scripts are executable with valid
POSIX `sh` syntax, and there are no leftover merge-conflict markers, and runs
the Python unit tests. It's the same check CI runs. Also run:

```sh
tests/shell/test-helpers                  # firewall/Secure Boot/package-list helpers
sudo tests/shell/test-restore-root        # restore points (scratch /etc)
sudo tests/shell/test-snapshot-root       # Btrfs snapshots (loop device; skips without one)
tools/gui-smoke-test                      # every app in a headless Sway (CI does this)
```

Rules the new apps follow: configuration is KEY=VALUE, parsed never sourced;
every package operation goes through `luxenos-pkg-root` (one lock); nothing
is deleted or flashed without a confirmation; optional hardware or programs
degrade to "unavailable"; only describe as tested what was actually run.

If you're touching a hook or the kiosk/Calamares flow, also try a real
build — `sh -n` only catches syntax errors, not runtime behavior:

```sh
sudo apt install live-build
sudo lb clean --purge
sudo lb config
sudo lb build
qemu-system-x86_64 -m 4096 -enable-kvm -cdrom live-image-amd64.hybrid.iso
```

A full build can take 30–90 minutes.

## Style

- Shell scripts target POSIX `sh`, not bash-specific syntax — live-build
  hooks run under `/bin/sh` in the chroot.
- Keep hook output prefixed and logged (see the `log()` helpers in existing
  hooks) so build logs are easy to scan.
- Match the existing palette (`#1d2330`, `#2e3650`, `#5e81ac`, `#e0e6f0`)
  for any new UI/branding work.
- Avoid marketing superlatives ("beats macOS," "fastest ever") in code
  comments or shipped strings without something to back them up. Positioning
  copy belongs in the README, where it's clearly framed as positioning, not
  benchmark claims.

## Reporting issues

Include the exact `lb config`/`lb build` command you ran, the relevant
section of the build log, and your host distro/architecture.

## Important note
Alwa6s change the YAML file so it puts the correct assets in the release

