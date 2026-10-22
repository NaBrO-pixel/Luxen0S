"""Shared code for the LuxenOS desktop applications.

Everything here follows the conventions of the shell tools next to it:
configuration is KEY=VALUE text that is parsed, never executed; privileged
work goes through the small root helpers in /usr/libexec/luxenos (started by
pkexec, which validates the caller with polkit); and every optional feature
degrades to "unavailable" instead of failing.

Modules that need GTK import it lazily (luxenos.ui), so the pure-logic
modules can be unit tested without a display.
"""

VERSION_FILE = "/etc/luxenos-version"


def os_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"
