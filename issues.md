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
