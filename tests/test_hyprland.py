import json
import subprocess
import unittest
from unittest.mock import patch

from zulip_hub.config import (
    AccountConfig, BridgeConfig, OpenConfig, WorkspaceConfig,
)
from zulip_hub.hyprland import HyprlandController, HyprlandError, resolve_launch_command


class FakeHyprland:
    def __init__(self, clients=None, visible=False):
        self.client_rows = clients or []
        self.visible = visible
        self.commands = []

    def run(self, command, **_kwargs):
        self.commands.append(command)
        if command == ["hyprctl", "-j", "clients"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.client_rows), "")
        if command == ["hyprctl", "-j", "monitors"]:
            name = "special:zulip" if self.visible else ""
            return subprocess.CompletedProcess(
                command, 0, json.dumps([{"specialWorkspace": {"name": name}}]), ""
            )
        if command[:2] == ["hyprctl", "dispatch"]:
            return subprocess.CompletedProcess(command, 0, "ok", "")
        raise AssertionError(command)


class FakeSpawn:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        return object()


def client(workspace="special:zulip"):
    return {
        "address": "0xabc", "class": "zulip-hub", "initialClass": "zulip-hub",
        "title": "Zulip", "workspace": {"name": workspace},
    }


class HyprlandTests(unittest.TestCase):
    def controller(self, fake, spawn=None):
        return HyprlandController(
            WorkspaceConfig(), ["omarchy-launch-webapp", "https://chat.example.com"],
            run=fake.run, spawn=spawn or FakeSpawn(),
        )

    def test_visible_workspace_is_hidden_without_launch(self):
        fake = FakeHyprland([client()], visible=True)
        spawn = FakeSpawn()
        self.assertEqual(self.controller(fake, spawn).toggle(), "hidden")
        self.assertEqual(spawn.calls, [])
        self.assertIn(
            ["hyprctl", "dispatch", "togglespecialworkspace", "zulip"], fake.commands
        )

    def test_existing_client_is_moved_then_workspace_shown(self):
        fake = FakeHyprland([client("1")])
        self.assertEqual(self.controller(fake).toggle(), "shown")
        dispatches = [row for row in fake.commands if row[:2] == ["hyprctl", "dispatch"]]
        self.assertEqual(dispatches, [
            ["hyprctl", "dispatch", "movetoworkspacesilent", "special:zulip,address:0xabc"],
            ["hyprctl", "dispatch", "togglespecialworkspace", "zulip"],
        ])

    def test_missing_client_opens_workspace_and_launches(self):
        fake = FakeHyprland()
        spawn = FakeSpawn()
        self.assertEqual(self.controller(fake, spawn).toggle(), "launched")
        self.assertEqual(spawn.calls[0][0][0], "omarchy-launch-webapp")

    def test_failed_launch_hides_empty_workspace_again(self):
        fake = FakeHyprland()
        controller = self.controller(fake, FakeSpawn(OSError("missing")))
        with self.assertRaises(HyprlandError):
            controller.toggle()
        toggles = [row for row in fake.commands if "togglespecialworkspace" in row]
        self.assertEqual(len(toggles), 2)

    def test_class_detection_checks_initial_class(self):
        row = client()
        row["class"] = "electron"
        fake = FakeHyprland([row])
        self.assertEqual(self.controller(fake).matching_client()["address"], "0xabc")

    @patch("zulip_hub.hyprland.commands.available")
    def test_launch_resolution_prefers_desktop_then_webapp(self, available):
        config = BridgeConfig(
            AccountConfig("https://chat.example.com", "me@example.com"),
            opening=OpenConfig("auto", ("zulip",)),
        )
        available.side_effect = lambda command: command in {"zulip", "uwsm-app"}
        self.assertEqual(resolve_launch_command(config), ["uwsm-app", "--", "zulip"])

        available.side_effect = lambda command: command == "omarchy-launch-webapp"
        config = BridgeConfig(AccountConfig("https://chat.example.com", "me@example.com"))
        self.assertEqual(resolve_launch_command(config), [
            "omarchy-launch-webapp", "https://chat.example.com", "--class=zulip-hub",
        ])



class FocusWindowTests(unittest.TestCase):
    def controller(self, codes):
        from zulip_hub.commands import CommandResult
        from zulip_hub.config import WorkspaceConfig
        from zulip_hub.hyprland import HyprlandController
        calls = []

        def run(argv, timeout):
            calls.append(argv)
            return CommandResult(codes.pop(0), "")

        return HyprlandController(WorkspaceConfig(), [], run=run), calls

    def test_lua_syntax_first(self):
        controller, calls = self.controller([0])
        controller.focus_window("0xabc")
        self.assertEqual(calls, [["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xabc" })']])

    def test_falls_back_to_classic_dispatch(self):
        controller, calls = self.controller([1, 0])
        controller.focus_window("0xabc")
        self.assertEqual(calls[-1], ["hyprctl", "dispatch", "focuswindow", "address:0xabc"])

    def test_address_is_validated_before_reaching_lua(self):
        from zulip_hub.hyprland import HyprlandError
        controller, calls = self.controller([0])
        with self.assertRaises(HyprlandError):
            controller.focus_window('0x1" }) os.execute("x') 
        self.assertEqual(calls, [])
