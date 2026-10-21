#!/bin/sh
# Run in the installed target (Calamares' shellprocess executes this).
# Enable greetd so the installed system boots to a greeter.
set -eu

log() { echo "[luxenos-enable-greetd] $*"; }

SYSTEMCTL=/bin/systemctl
if [ ! -x "$SYSTEMCTL" ]; then
    SYSTEMCTL=/usr/bin/systemctl
fi

# Only proceed if systemctl exists in the target and greetd is installed.
if [ -x "$SYSTEMCTL" ]; then
    if [ -x /usr/bin/greetd ] || [ -x /usr/sbin/greetd ]; then
        log "Reloading systemd unit files"
        "$SYSTEMCTL" daemon-reload || {
            log "ERROR: systemctl daemon-reload failed"
            exit 1
        }

        log "Enabling and starting greetd.service"
        if ! "$SYSTEMCTL" enable --now greetd.service >/dev/null 2>&1; then
            log "ERROR: could not enable/start greetd.service"
            exit 1
        fi

        if ! "$SYSTEMCTL" is-active --quiet greetd.service; then
            log "ERROR: greetd.service is not active"
            exit 1
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
    exit 1
fi

exit 0
