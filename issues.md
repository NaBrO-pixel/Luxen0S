# Code review findings for LuxenOS

Static review of build, install, and login-path correctness. Status as of the
latest review pass.

## Fixed

1. **Calamares cleanup job could fail the install** - `enable-greetd.sh` was
   committed without the executable bit, and the `shellprocess` job runs it by
   path, so it died with "Permission denied" after the real work was done.
   Now mode 755; hook 0250 chmods everything in `/usr/libexec/luxenos/`, and
   `tools/validate-release-tree` checks the bit.
2. **greetd's Sway had no software-renderer fallback** - `/etc/profile.d` is not
   sourced by greetd, so on llvmpipe-only machines (most VMs) the greeter never
   appeared. greetd and wlgreet now start Sway through
   `/usr/local/bin/luxenos-sway-session`, which sets `WLR_RENDERER_ALLOW_SOFTWARE`
   (and the software cursor in VMs) and prefers the bundled SwayFX.
3. **Auto-update timer ran twice a day** - `OnCalendar=daily` and
   `OnCalendar=*-*-* 02:00:00` are two separate triggers (00:00 and 02:00).
   Only the 02:00 one is kept.
4. **Dead cron fallback in hook 0300** - it only ran when `/etc/cron.d` did not
   exist, then tried to write into it. Removed (systemd is the only init).
5. **Validator gaps** - hook 0200-build-swayfx was never checked, the bash
   updater scripts were only parsed with `sh -n`, and nothing asserted the
   greetd wiring. All covered now.
6. Stale comments in `calamares/settings.conf` (claimed no display manager) and
   `profile.d/luxenos-session.sh` (dangling comment fragment).
7. **Auto-update ran at every boot** - the timer had
   `Requires=luxenos-auto-update.service`, which starts the service whenever
   the timer starts. Removed (the validator rejects it).
8. **`luxenos-updater` ignored `updater.conf`** - firmware/Waydroid/Flatpak
   toggles, backup settings and AC-only were never read. It now parses them;
   apt failures are detected (pipelines returned `tee`'s status), upgrades use
   `--with-new-pkgs` so new kernel packages are not held back, the
   "security only" step (a no-op `apt-get install --only-upgrade`) now runs
   `unattended-upgrade`, and `fwupdmgr` no longer waits for a prompt.
9. **`luxenos-update-settings`** - `AUTO_UPDATE_TIME` never reached the timer
   (now a drop-in), and pressing Enter at "Enable automatic updates?" in
   `configure` disabled them.
10. **Traditional installs never enabled greetd** - preseed's `late_command`
    now runs `enable-greetd.sh` as Calamares does.
11. **No wallpaper** - `swaybg` (only a Recommends of sway) was missing from the
    package lists; `flatpak` and `fwupd`, used by hooks/updater/health check,
    were missing too.
12. **Android apps had no network** - ufw's deny-incoming / DROP-forward
    blocked Waydroid's DHCP, DNS and NAT on `waydroid0`; hook 0200 allows them.
13. **Power profile never applied** - `sudo -n tlp` has no sudoers rule. Now a
    polkit action (`org.luxenos.power.profile`, active session, no password)
    runs `luxenos-power-profile-root`, which accepts only `bat|ac|start`. The
    Waybar module also listens for the refresh signal now.
14. Smaller: installer error dialog's "Retry" just exited and showed literal
    `\n`; `luxenos-setup-android` hung on the foreground
    `waydroid session start`; Print took two separate screenshots; the
    validator parsed bash scripts with `sh -n`.

## Already resolved earlier (original findings)

- greetd enablement: done with `systemctl --root=/ enable` plus verification
  (starting a service inside the install chroot is not possible).
- tty1-only autostart: the console-login fallback now accepts any local VT.
- Live-access username: validated against the configured live user.
- ISO artifact validation: `build-iso.yml` checks the ISO and checksums before upload.
- README no longer says "Don't use".

## Still open

- Installer-to-installed-system handoff (live -> Calamares -> greetd) has never
  been exercised in a real install; add a QEMU smoke test in CI.
- `luxenos-updater` / `luxenos-update-settings` have shellcheck SC2155 style
  warnings (declare-and-assign on one line masks exit codes).
