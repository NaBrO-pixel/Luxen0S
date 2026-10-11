# LuxenOS: start the desktop when a normal user logs in on the first console.
# Sourced by /etc/profile, so it must stay POSIX sh.
#
#   installed system / live "desktop" session   ->  sway (~/.config/sway/config)
#   live USB, default                           ->  sway with the installer kiosk
#                                                   config (Calamares only)
#
# Opt out: touch ~/.config/luxenos/no-autostart
# Live USB: boot with luxenos.session=desktop to get the full desktop instead
#           of the installer kiosk.

if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ] \
    && [ "$(id -u)" -ne 0 ] \
    && [ "$(tty 2>/dev/null)" = "/dev/tty1" ] \
    && [ ! -e "${HOME:-/nonexistent}/.config/luxenos/no-autostart" ] \
    && command -v sway >/dev/null 2>&1; then

    export XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland XDG_SESSION_DESKTOP=sway

    # Machines without a working GPU driver (most VMs, brand-new GPUs) only
    # have Mesa's software renderer (llvmpipe), which wlroots refuses unless
    # told otherwise -- Sway then exits and the console is all you see. Has
    # no effect when a hardware renderer is available. VMs also often draw no
    # hardware cursor.
    export WLR_RENDERER_ALLOW_SOFTWARE=1
    if command -v systemd-detect-virt >/dev/null 2>&1 && systemd-detect-virt -q --vm 2>/dev/null; then
        export WLR_NO_HARDWARE_CURSORS=1
    fi

    if grep -qw 'boot=live' /proc/cmdline 2>/dev/null \
        && ! grep -qw 'luxenos.session=desktop' /proc/cmdline 2>/dev/null \
        && [ -r /etc/luxenos-installer/kiosk-sway-config ]; then
        exec sway -c /etc/luxenos-installer/kiosk-sway-config
    fi
    exec sway
fi
