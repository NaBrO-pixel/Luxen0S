#!/bin/sh
# Run in the installed target (Calamares' shellprocess executes this).
# Enable greetd so the installed system boots to a greeter.
set -eu

log() { echo "[luxenos-enable-greetd] $*"; }

# Only proceed if systemctl exists in the target and greetd is installed.
if [ -x /bin/systemctl ] || [ -x /usr/bin/systemctl ]; then
    if [ -x /usr/bin/greetd ] || [ -x /usr/sbin/greetd ]; then
        log "Enabling greetd.service"
        if ! systemctl enable greetd.service >/dev/null 2>&1; then
            log "Warning: could not enable greetd.service"
        fi
        if [ -f /etc/systemd/system/getty@tty1.service.d/luxenos-autologin.conf ]; then
            log "Removing live autologin drop-in from installed system"
            rm -f /etc/systemd/system/getty@tty1.service.d/luxenos-autologin.conf || true
        fi
    else
        log "greetd is not installed in the image; skipping enable"
    fi
else
    log "systemctl not available; cannot enable display manager"
fi

exit 0
