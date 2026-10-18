import unittest
from unittest.mock import patch

from zulip_hub.config import OpenConfig
from zulip_hub.opening import OpenError, UrlOpener


class OpeningTests(unittest.TestCase):
    @patch("zulip_hub.opening.commands.available")
    def test_auto_prefers_configured_desktop_client(self, available):
        available.side_effect = lambda command: command == "zulip"
        command = UrlOpener(OpenConfig("auto", ("zulip", "--new-window"))).command("https://chat/#narrow/x")
        self.assertEqual(command, ["zulip", "--new-window", "https://chat/#narrow/x"])

    @patch("zulip_hub.opening.commands.available")
    def test_auto_prefers_the_webapp_window(self, available):
        available.side_effect = lambda command: command in {"omarchy-launch-webapp", "uwsm-app"}
        command = UrlOpener(OpenConfig()).command("https://chat/#narrow/x")
        self.assertEqual(command, ["omarchy-launch-webapp", "https://chat/#narrow/x"])

    @patch("zulip_hub.opening.commands.available")
    def test_browser_mode_skips_the_webapp(self, available):
        available.side_effect = lambda command: command in {"omarchy-launch-webapp", "uwsm-app"}
        command = UrlOpener(OpenConfig("browser")).command("https://chat/#narrow/x")
        self.assertEqual(command, ["uwsm-app", "--", "xdg-open", "https://chat/#narrow/x"])

    @patch("zulip_hub.opening.commands.available")
    def test_auto_falls_back_to_uwsm_browser(self, available):
        available.side_effect = lambda command: command == "uwsm-app"
        command = UrlOpener(OpenConfig()).command("https://chat/#narrow/x")
        self.assertEqual(command, ["uwsm-app", "--", "xdg-open", "https://chat/#narrow/x"])

    @patch("zulip_hub.opening.commands.available", return_value=False)
    def test_desktop_mode_fails_closed(self, _available):
        with self.assertRaises(OpenError):
            UrlOpener(OpenConfig("desktop", ("zulip",))).command("https://chat/#narrow/x")



class FakeHyprland:
    def __init__(self, clients):
        self._clients = clients
        self.dispatched = []

    def clients(self):
        return self._clients

    def focus_window(self, address):
        self.dispatched.append(("focus", address))


class AccountOpenerTests(unittest.TestCase):
    CLIENTS = [
        {"class": "chrome-fstar.zulipchat.com.evil.org__-Default", "address": "0xbad"},
        {"class": "chrome-fstar.zulipchat.com__-Default", "address": "0xabc"},
        {"class": "foot", "address": "0x1"},
    ]

    def test_app_window_matches_exact_host(self):
        from zulip_hub.opening import app_window
        self.assertEqual(app_window(self.CLIENTS, "https://fstar.zulipchat.com")["address"], "0xabc")
        self.assertIsNone(app_window(self.CLIENTS, "https://zulipchat.com"))

    @patch("zulip_hub.opening.commands.spawn")
    def test_existing_window_is_focused(self, spawn):
        from zulip_hub.opening import AccountOpener
        hyprland = FakeHyprland(self.CLIENTS)
        self.assertEqual(AccountOpener(hyprland).open("https://fstar.zulipchat.com"), "focused")
        self.assertEqual(hyprland.dispatched, [("focus", "0xabc")])
        spawn.assert_not_called()

    @patch("zulip_hub.opening.commands.available", return_value=True)
    @patch("zulip_hub.opening.commands.spawn")
    def test_missing_window_is_launched(self, spawn, _available):
        from zulip_hub.opening import AccountOpener
        hyprland = FakeHyprland(self.CLIENTS)
        self.assertEqual(AccountOpener(hyprland).open("https://zulip.example.org"), "launched")
        spawn.assert_called_once_with(["omarchy-launch-webapp", "https://zulip.example.org"])
        self.assertEqual(hyprland.dispatched, [])
