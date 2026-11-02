# LuxenOS release notes

## Unreleased (dev branch) - feature expansion and Debian 14 preparation

Base: Debian 13 "trixie" (unchanged default). Everything below was added on
top of the existing desktop; no existing app, hook or file association was
removed.

### New applications
- **Update Manager** (`luxenos-update-manager`, replaces the terminal-only
  "LuxenOS Updater" launcher entry; `luxenos-updater` stays for terminals):
  Debian/security/Flatpak/firmware/Android updates, download-only, history,
  logs, schedule, daily "updates available" notification (user timer).
- **Hardware** (`luxenos-hardware`): devices, drivers, firmware versions,
  compatibility checks, Secure Boot status, copyable report.
- **Notifications** (`luxenos-notifications`, Super+Shift+N): history,
  grouping, per-app allow/silent/block, sounds, Do Not Disturb.
- **Personalization** (`luxenos-personalize`): the whole look in one window,
  writing only through `luxenos-glass`, theme packs and the wallpaper engine.
- **Desktop widgets** (`luxenos-widgets`): clock/calendar, weather
  (Open-Meteo, no account), CPU, memory, battery, network, storage, music.
- **Android** (`luxenos-android`), **Phone Connect** (`luxenos-phone`, KDE
  Connect on request), **Windows Programs** (`luxenos-wine-manager`),
  **Plugins** (`luxenos-plugins`), **Assistant** (`luxenos-assistant`,
  Super+F1, offline).

### Improved
- Launcher: calculator, unit conversion, settings search, web shortcuts,
  recent searches, typo-tolerant app search, plugin results.
- Workspace overview: previews of on-screen windows, rename/close
  workspaces, per-workspace wallpapers, arrow keys.
- Restore Center: Btrfs snapshots page (create, delete, retention, rollback
  where the layout allows it); pre-update snapshots.
- Software Center: AppStream names/descriptions/screenshots, Flatpak page,
  Debian/Flathub duplicates linked instead of listed twice.
- `.exe` handler: per-program Wine prefixes (`--prefix`, `wine-apps.list`).
- `luxenos-glass`: icon theme, cursor theme/size, bar position.
- Wallpaper engine (`luxenos-wallpaper`): per monitor, per workspace,
  slideshow; theme packs use it.
- `luxenos-updater` takes the shared package lock and honours
  `DOWNLOAD_ONLY`; Welcome no longer claims updates are automatic by default.

### Debian 14
- Package lists are release-aware (live-build conditionals);
  `tools/expand-package-lists`; CI resolves trixie and (informationally) forky.
- `DISTRIBUTION=forky` requires `LUXENOS_EXPERIMENTAL_FORKY=1`. Forky
  differences: no Wine, no wlgreet (gtkgreet fallback), papers instead of
  evince, no apparmor-profiles-extra. Not built or booted.

### Security
- New root helpers, each with a polkit action (administrator password) and a
  fixed set of validated subcommands: snapshots, Android container,
  firewall (phone ports for one private network only).
- Plugins run sandboxed (bubblewrap) as the user; the assistant runs only
  whitelisted read-only diagnostics, on a click.
- Secure Boot: status/boot-chain check; opt-in signed build
  (`LUXENOS_SECURE_BOOT=1`), untested on firmware.

### New packages
`pciutils`, `usbutils`, `mokutil`, `appstream`, `gir1.2-appstream-1.0`,
`sound-theme-freedesktop`, `bubblewrap`, `network-manager-applet` +
`nm-connection-editor` (replacing the transitional `network-manager-gnome`);
forky only: `gtkgreet`, `papers`. Optional on request: `kdeconnect`.

### Known limitations
See README.md, "Known limitations": the new apps were tested by unit tests
and a headless GTK start only, not on real Sway/hardware; no ISO was built.
