import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHROOT = os.path.join(ROOT, "config", "includes.chroot")
sys.path.insert(0, os.path.join(CHROOT, "usr", "lib", "luxenos", "python"))
