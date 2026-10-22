"""Minimal Sway (i3-compatible) IPC client, standard library only.

Used by the dock and the workspace overview. Only documented message types
are used (RUN_COMMAND, GET_WORKSPACES, SUBSCRIBE, GET_OUTPUTS, GET_TREE), so
it works with stock Sway and SwayFX alike and needs no X11.
"""

import json
import os
import socket
import struct

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=6sII")

RUN_COMMAND = 0
GET_WORKSPACES = 1
SUBSCRIBE = 2
GET_OUTPUTS = 3
GET_TREE = 4

EVENT_BIT = 1 << 31


class IPCError(Exception):
    pass


def socket_path():
    path = os.environ.get("SWAYSOCK")
    if path:
        return path
    raise IPCError("SWAYSOCK is not set: not running inside Sway")


def pack(msg_type, payload=b""):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return HEADER.pack(MAGIC, len(payload), msg_type) + payload


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise IPCError("Sway closed the IPC connection")
        buf += chunk
    return buf


def read_message(sock):
    magic, length, msg_type = HEADER.unpack(_recv_exact(sock, HEADER.size))
    if magic != MAGIC:
        raise IPCError("bad IPC reply")
    body = _recv_exact(sock, length)
    return msg_type, json.loads(body.decode("utf-8", "replace"))


class Connection:
    def __init__(self, path=None, timeout=5.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(path or socket_path())
        except OSError as e:
            self.sock.close()
            raise IPCError("cannot connect to Sway: %s" % e) from e

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def request(self, msg_type, payload=b""):
        self.sock.sendall(pack(msg_type, payload))
        while True:
            rtype, data = read_message(self.sock)
            if not rtype & EVENT_BIT:
                return data

    def command(self, cmd):
        """Run a Sway command; raises IPCError with Sway's message on failure."""
        results = self.request(RUN_COMMAND, cmd)
        for r in results:
            if not r.get("success", False):
                raise IPCError(r.get("error") or "command failed: %s" % cmd)
        return results

    def get_tree(self):
        return self.request(GET_TREE)

    def get_workspaces(self):
        return self.request(GET_WORKSPACES)

    def get_outputs(self):
        return self.request(GET_OUTPUTS)

    def subscribe(self, events):
        """Switch this connection to event mode. Use a dedicated connection."""
        reply = self.request(SUBSCRIBE, json.dumps(list(events)))
        if not reply.get("success"):
            raise IPCError("subscription refused")
        self.sock.settimeout(None)


def command(cmd):
    with Connection() as c:
        return c.command(cmd)


# --- tree helpers (pure functions, unit tested) -------------------------------

def is_window(node):
    """Application windows: native Wayland (app_id) or Xwayland (window_properties)."""
    if node.get("type") not in ("con", "floating_con"):
        return False
    if node.get("nodes") or node.get("floating_nodes"):
        return False
    return bool(node.get("app_id") or node.get("window_properties") or node.get("pid"))


def window_app_key(node):
    """The identity used to group windows of one application."""
    app_id = node.get("app_id")
    if app_id:
        return app_id
    props = node.get("window_properties") or {}
    return props.get("class") or props.get("instance") or ""


def iter_windows(tree):
    """Yield (window, workspace_name, output_name) for every window.

    Scratchpad ("minimized") windows have workspace "__i3_scratch".
    """
    def walk(node, ws, out):
        ntype = node.get("type")
        if ntype == "output":
            out = node.get("name")
        elif ntype == "workspace":
            ws = node.get("name")
        if is_window(node):
            yield node, ws, out
            return
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            yield from walk(child, ws, out)

    yield from walk(tree, None, None)


def workspaces_with_windows(tree):
    """[{name, num, output, focused, visible, windows: [node...]}] in tree order,
    without the scratchpad pseudo-workspace."""
    result = []

    def walk(node, out):
        ntype = node.get("type")
        if ntype == "output":
            out = node.get("name")
        if ntype == "workspace":
            name = node.get("name")
            if name == "__i3_scratch":
                return
            wins = [w for w, _, _ in iter_windows(node)]
            result.append({
                "name": name,
                "num": node.get("num", -1),
                "output": out,
                "focused": _subtree_focused(node),
                "windows": wins,
            })
            return
        for child in node.get("nodes", []):
            walk(child, out)

    walk(tree, None)
    return result


def _subtree_focused(node):
    if node.get("focused"):
        return True
    return any(_subtree_focused(c) for c in node.get("nodes", []) + node.get("floating_nodes", []))


def focused_window(tree):
    for w, _, _ in iter_windows(tree):
        if w.get("focused"):
            return w
    return None


def quote_name(name):
    """Quote a workspace name for a Sway command."""
    return '"%s"' % str(name).replace("\\", "\\\\").replace('"', '\\"')


def con_criteria(con_id):
    return "[con_id=%d]" % int(con_id)
