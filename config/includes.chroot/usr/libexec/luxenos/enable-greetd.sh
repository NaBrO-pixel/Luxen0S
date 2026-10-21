#!/bin/sh
# Runs inside the installed target chroot during Calamares cleanup.
# Enable greetd for the next boot; do not try to start a service inside a chroot.
set -eu

log() { echo "[luxenos-enable-greetd] $*"; }

SYSTEMCTL=$(command -v systemctl || true)
if [ -z "$SYSTEMCTL" ]; then
    log "ERROR: systemctl is missing from the installed target"
    exit 1
fi

if ! command -v greetd >/dev/null 2>&1; then
    log "ERROR: greetd is not installed in the target"
    exit 1
fi

if [ ! -f /etc/greetd/config.toml ]; then
    log "ERROR: /etc/greetd/config.toml is missing"
    exit 1
fi

# wlgreet runs under the dedicated greetd account and needs access to graphics devices.
if command -v usermod >/dev/null 2>&1; then
    for group in video render; do
        if getent group "$group" >/dev/null 2>&1; then
            usermod -a -G "$group" _greetd
        fi
    done
fi

# --root writes enablement links into the target filesystem without contacting
# a running systemd manager (which is not available inside the install chroot).
log "Enabling greetd for the next boot"
if ! "$SYSTEMCTL" --root=/ enable greetd.service; then
    log "ERROR: could not enable greetd.service in the installed target"
    exit 1
fi

if ! "$SYSTEMCTL" --root=/ is-enabled --quiet greetd.service; then
    log "ERROR: greetd.service did not become enabled"
    exit 1
fi

if [ -f /etc/systemd/system/getty@tty1.service.d/luxenos-autologin.conf ]; then
    log "Removing live autologin drop-in from installed system"
    rm -f /etc/systemd/system/getty@tty1.service.d/luxenos-autologin.conf
fi

exit 0
