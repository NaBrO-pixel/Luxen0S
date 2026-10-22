import json
import socket
import threading
import unittest

import helpers
from luxenos import dock, overview, swayipc
from luxenos.apps import AppEntry, AppIndex

TREE = {
    "type": "root", "nodes": [
        {"type": "output", "name": "__i3", "nodes": [
            {"type": "workspace", "name": "__i3_scratch", "nodes": [], "floating_nodes": [
                {"type": "floating_con", "id": 40, "app_id": "foot", "name": "mini", "nodes": [], "floating_nodes": []}]}]},
        {"type": "output", "name": "eDP-1", "nodes": [
            {"type": "workspace", "name": "1", "num": 1, "nodes": [
                {"type": "con", "id": 10, "app_id": "firefox-esr", "name": "Web", "focused": True,
                 "nodes": [], "floating_nodes": []},
                {"type": "con", "id": 11, "nodes": [
                    {"type": "con", "id": 12, "app_id": "foot", "name": "t1", "nodes": [], "floating_nodes": []},
                    {"type": "con", "id": 13, "window_properties": {"class": "Gimp"}, "name": "gimp",
                     "nodes": [], "floating_nodes": []}], "floating_nodes": []}],
             "floating_nodes": []},
            {"type": "workspace", "name": "2: code \"x\"", "num": 2, "nodes": [], "floating_nodes": []}]},
    ]}


assert helpers.CHROOT  # imported for its sys.path setup


class TreeTest(unittest.TestCase):
    def test_iter_windows_includes_scratchpad_and_xwayland(self):
        found = {(w["id"], ws) for w, ws, _ in swayipc.iter_windows(TREE)}
        self.assertEqual(found, {(40, "__i3_scratch"), (10, "1"), (12, "1"), (13, "1")})

    def test_workspaces_skip_scratchpad(self):
        wss = swayipc.workspaces_with_windows(TREE)
        self.assertEqual([w["name"] for w in wss], ["1", "2: code \"x\""])
        self.assertTrue(wss[0]["focused"])
        self.assertEqual(len(wss[0]["windows"]), 3)
        self.assertEqual(wss[1]["windows"], [])

    def test_quoting(self):
        self.assertEqual(swayipc.quote_name('a "b" \\c'), '"a \\"b\\" \\\\c"')
        self.assertEqual(swayipc.con_criteria("12"), "[con_id=12]")
        with self.assertRaises(ValueError):
            swayipc.con_criteria("12; exec rm")

    def test_overview_commands(self):
        wss = swayipc.workspaces_with_windows(TREE)
        self.assertEqual(overview.workspace_cmd(wss[0]), "workspace number 1")
        self.assertEqual(overview.workspace_cmd(wss[1]), 'workspace "2: code \\"x\\""')
        self.assertEqual(overview.next_free_workspace(wss), 3)
        self.assertEqual(overview.move_cmd(10, number=3), "[con_id=10] move container to workspace number 3")

    def test_ipc_roundtrip(self):
        a, b = socket.socketpair()

        def server():
            hdr = b.recv(14)
            magic, length, mtype = swayipc.HEADER.unpack(hdr)
            body = b.recv(length) if length else b""
            reply = json.dumps([{"success": body == b"nop"}]).encode()
            b.sendall(swayipc.HEADER.pack(magic, len(reply), mtype) + reply)

        t = threading.Thread(target=server)
        t.start()
        conn = swayipc.Connection.__new__(swayipc.Connection)
        conn.sock = a
        self.assertEqual(conn.command("nop"), [{"success": True}])
        t.join()
        a.close()
        b.close()


class DockTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            AppEntry("firefox-esr.desktop", "Firefox", exec_name="firefox-esr"),
            AppEntry("foot.desktop", "Foot", exec_name="foot"),
            AppEntry("gimp.desktop", "GIMP", exec_name="gimp-2.10", wm_class="gimp"),
            AppEntry("thunar.desktop", "Thunar", exec_name="thunar", visible=False),
            AppEntry("luxenos-files.desktop", "Files", exec_name="thunar"),
            AppEntry("org.luxenos.SoftwareCenter.desktop", "Software"),
        ]
        self.index = AppIndex(self.entries)
        self.by_id = {e.app_id: e for e in self.entries}

    def test_matching(self):
        self.assertEqual(self.index.lookup("Gimp").app_id, "gimp.desktop")
        self.assertEqual(self.index.lookup("foot").app_id, "foot.desktop")
        self.assertEqual(self.index.lookup("thunar").app_id, "thunar.desktop")
        self.assertEqual(self.index.by_exec["thunar"].app_id, "luxenos-files.desktop")
        self.assertEqual(self.index.lookup("org.luxenos.SoftwareCenter").name, "Software")
        self.assertIsNone(self.index.lookup("unknown-app"))

    def test_grouping_pinned_first_and_minimized(self):
        items = dock.build_items(TREE, ["foot.desktop", "missing.desktop"], self.index, self.by_id)
        self.assertEqual([i.key for i in items], ["foot.desktop", "firefox-esr.desktop", "gimp.desktop"])
        foot = items[0]
        self.assertTrue(foot.pinned)
        self.assertEqual(len(foot.windows), 2)
        self.assertEqual(len(foot.minimized_windows()), 1)
        self.assertTrue(items[1].focused)

    def test_next_window_cycles(self):
        item = dock.DockItem("x", None, False)
        item.windows = [({"id": 1}, "1"), ({"id": 2, "focused": True}, "1"), ({"id": 3}, "2")]
        self.assertEqual(dock.next_window(item)["id"], 3)
        item.windows[2] = ({"id": 3}, dock.SCRATCH)
        self.assertEqual(dock.next_window(item)["id"], 1)

    def test_settings_validation(self):
        s = dock.parse_settings({"DOCK_ICON_SIZE": "500", "DOCK_POSITION": "top", "DOCK_PINNED": "a.desktop b;rm"})
        self.assertEqual(s["DOCK_ICON_SIZE"], 44)
        self.assertEqual(s["DOCK_POSITION"], "bottom")
        self.assertEqual(s["DOCK_PINNED"], ["a.desktop"])
        self.assertEqual(dock.validate_setting("DOCK_AUTOHIDE", "on"), "yes")
        for k, v in (("DOCK_ICON_SIZE", "12"), ("DOCK_POSITION", "top"), ("NOPE", "1"),
                     ("DOCK_PINNED", "x.desktop y"), ("DOCK_AUTOHIDE", "maybe")):
            with self.assertRaises(ValueError):
                dock.validate_setting(k, v)


if __name__ == "__main__":
    unittest.main()
