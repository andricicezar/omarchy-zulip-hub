import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zulip_hub.commands import CommandResult
from zulip_hub.launchers import WebappLaunchers, launcher_name
from zulip_hub.onboarding import OnboardingManager


class FakeSecrets:
    def __init__(self):
        self.values = {}

    def get(self, site, email):
        from zulip_hub.secrets import SecretError
        try:
            return self.values[(site, email)]
        except KeyError as exc:
            raise SecretError("missing") from exc

    def store(self, site, email, key):
        self.values[(site, email)] = key

    def delete(self, site, email):
        self.values.pop((site, email), None)


class LauncherCase(unittest.TestCase):
    """Toute commande est simulée : aucun lanceur réel n’est jamais touché."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.applications = self.root / "applications"
        self.applications.mkdir()
        self.calls = []
        self.fail_with_icon = False

        def run(argv, **kwargs):
            self.calls.append(argv)
            assert kwargs["env_extra"] == {"OMARCHY_REMOVE_NOTIFY": "false"}
            if argv[0] == "omarchy-webapp-install" and self.fail_with_icon and argv[3]:
                return CommandResult(1, "Error: Failed to download icon.")
            return CommandResult(0, "")

        for target, value in [
            ("zulip_hub.launchers.commands.run", {"side_effect": run}),
            ("zulip_hub.launchers.commands.available", {"return_value": True}),
            ("zulip_hub.launchers.WebappLaunchers._icon", {"return_value": "https://a.org/icon.png"}),
            ("zulip_hub.launchers.WebappLaunchers.active", {"return_value": True}),
        ]:
            patcher = patch(target, **value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.launchers = WebappLaunchers(self.root / "state", self.applications)

    def tearDown(self):
        self.temporary.cleanup()

    def registry(self):
        return json.loads((self.root / "state" / "launchers.json").read_text())


class WebappLauncherTests(LauncherCase):
    def test_names(self):
        self.assertEqual(launcher_name("FStar"), "FStar Zulip")
        self.assertEqual(launcher_name("Team Zulip"), "Team Zulip")
        self.assertEqual(launcher_name("a/b"), "a b Zulip")
        self.assertEqual(launcher_name(""), "Zulip")

    def test_install_uses_realm_icon_and_records_launcher(self):
        self.assertTrue(self.launchers.install("https://a.org", "A"))
        self.assertEqual(self.calls, [
            ["omarchy-webapp-install", "A Zulip", "https://a.org", "https://a.org/icon.png"],
        ])
        self.assertEqual(self.registry(), {"https://a.org": "A Zulip"})

    def test_install_falls_back_to_site_icon(self):
        self.fail_with_icon = True
        self.assertTrue(self.launchers.install("https://a.org", "A"))
        self.assertEqual(self.calls[-1], ["omarchy-webapp-install", "A Zulip", "https://a.org", ""])

    def test_manual_launcher_for_same_site_is_replaced(self):
        (self.applications / "My A.desktop").write_text(
            '[Desktop Entry]\nExec=omarchy-launch-webapp "https://a.org/"\n')
        (self.applications / "Other.desktop").write_text(
            '[Desktop Entry]\nExec=omarchy-launch-webapp "https://b.org/"\n')
        self.launchers.install("https://a.org", "A")
        self.assertEqual(self.calls[0], ["omarchy-webapp-remove", "My A"])
        self.assertNotIn(["omarchy-webapp-remove", "Other"], self.calls)

    def test_rename_removes_previous_launcher(self):
        self.launchers.install("https://a.org", "A")
        self.launchers.install("https://a.org", "Alpha")
        self.assertIn(["omarchy-webapp-remove", "A Zulip"], self.calls)
        self.assertEqual(self.registry(), {"https://a.org": "Alpha Zulip"})

    def test_remove_only_touches_recorded_launchers(self):
        self.assertFalse(self.launchers.remove("https://unknown.org"))
        self.launchers.install("https://a.org", "A")
        self.assertTrue(self.launchers.remove("https://a.org"))
        self.assertEqual(self.calls[-1], ["omarchy-webapp-remove", "A Zulip"])
        self.assertEqual(self.registry(), {})

    def test_inactive_outside_installed_plugin(self):
        with patch("zulip_hub.launchers.WebappLaunchers.active", return_value=False):
            self.assertFalse(self.launchers.install("https://a.org", "A"))
        self.assertEqual(self.calls, [])


class OnboardingLauncherTests(LauncherCase):
    def setUp(self):
        super().setUp()
        self.config = self.root / "config.toml"
        source = Path(__file__).resolve().parents[1] / "config/config.example.toml"
        self.config.write_text(source.read_text())
        self.secrets = FakeSecrets()
        self.manager = OnboardingManager(self.config, self.secrets, self.root / "state" / "state.json")
        # Le répertoire des lanceurs simulés, pas celui de l’utilisateur.
        original = OnboardingManager.launchers.fget
        patcher = patch.object(
            OnboardingManager, "launchers",
            property(lambda manager: WebappLaunchers(original(manager).registry_path.parent,
                                                     self.applications)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        tester = patch("zulip_hub.onboarding.ZulipClient.test_connection", return_value={})
        tester.start()
        self.addCleanup(tester.stop)

    def add(self, site, name=""):
        return self.manager.setup({"site": site, "email": "me@a.org", "api_key": "k", "name": name})

    def installs(self):
        return [call for call in self.calls if call[0] == "omarchy-webapp-install"]

    def test_sign_in_adds_and_removal_drops_launcher(self):
        result = self.add("https://a.org", "A")
        self.assertIn("A Zulip", result["message"])
        self.add("https://b.org", "B")
        self.assertEqual([call[1] for call in self.installs()], ["A Zulip", "B Zulip"])
        self.manager.remove_account({"site": "https://a.org"})
        self.assertEqual(self.calls[-1], ["omarchy-webapp-remove", "A Zulip"])

    def test_disconnecting_last_account_drops_launcher(self):
        self.add("https://a.org", "A")
        self.manager.disconnect()
        self.assertEqual(self.calls[-1], ["omarchy-webapp-remove", "A Zulip"])

    def test_moving_account_to_new_site_replaces_launcher(self):
        self.add("https://a.org", "A")
        self.manager.setup({"site": "https://c.org", "email": "me@a.org", "api_key": "k",
                            "name": "A", "original_site": "https://a.org"})
        self.assertIn(["omarchy-webapp-remove", "A Zulip"], self.calls)
        self.assertEqual(self.registry(), {"https://c.org": "A Zulip"})

    def test_setting_off_removes_and_on_recreates(self):
        self.add("https://a.org", "A")
        settings = {
            "notifications_enabled": True, "private_messages": True, "mentions": True,
            "followed_topics": True, "other_messages": False, "hide_content_when_locked": True,
            "group_window_seconds": 10, "open_mode": "auto",
        }
        self.manager.save_settings({**settings, "launchers": False})
        self.assertEqual(self.calls[-1], ["omarchy-webapp-remove", "A Zulip"])
        self.assertIn("launchers = false", self.config.read_text())
        before = len(self.installs())
        self.add("https://b.org", "B")
        self.assertEqual(len(self.installs()), before)
        self.manager.save_settings({**settings, "launchers": True})
        self.assertEqual(sorted(call[1] for call in self.installs()[before:]), ["A Zulip", "B Zulip"])


if __name__ == "__main__":
    unittest.main()
