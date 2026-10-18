import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from zulip_hub.browser_login import (
    HANDLER_DESKTOP, LoginError, LoginManager, SchemeHandler, decrypt_api_key,
)
from zulip_hub.commands import CommandResult
from zulip_hub.onboarding import OnboardingManager, serve_once


# Copie de zerver/lib/mobile_auth_otp.py (serveur Zulip), pour vérifier le
# déchiffrement contre l’algorithme réel plutôt que contre le nôtre.
def xor_hex_strings(bytes_a, bytes_b):
    assert len(bytes_a) == len(bytes_b)
    return "".join(f"{int(x, 16) ^ int(y, 16):x}" for x, y in zip(bytes_a, bytes_b))


def otp_encrypt_api_key(api_key, otp):
    assert len(otp) == 64
    return xor_hex_strings(api_key.encode().hex(), otp)


API_KEY = "abcdefghijklmnopqrstuvwxyz012345"
SETTINGS = {
    "realm_name": "FStar",
    "email_auth_enabled": True,
    "external_authentication_methods": [
        {"name": "google", "display_name": "Google", "login_url": "/accounts/login/social/google"},
        {"name": "evil", "display_name": "Evil", "login_url": "//evil.example.com/steal"},
    ],
}


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


class FakeHandler:
    def __init__(self, previous="org.zulip.Zulip.desktop"):
        self.previous = previous
        self.registered = False
        self.restored = None

    def register(self):
        self.registered = True
        return self.previous

    def restore(self, previous):
        self.registered = False
        self.restored = previous


class DecryptTests(unittest.TestCase):
    def test_round_trip_with_server_algorithm(self):
        otp = "0f" * 32
        self.assertEqual(decrypt_api_key(otp_encrypt_api_key(API_KEY, otp), otp), API_KEY)

    def test_wrong_length_or_garbage_is_refused(self):
        otp = "0f" * 32
        with self.assertRaises(LoginError):
            decrypt_api_key("abc", otp)
        with self.assertRaises(LoginError):
            decrypt_api_key("zz" * 32, otp)
        # Masque erroné : le résultat n’est pas une clé imprimable.
        with self.assertRaises(LoginError):
            decrypt_api_key(otp_encrypt_api_key(API_KEY, otp), "f0" * 32)


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = root / "config.toml"
        self.state = root / "state" / "state.json"
        source = Path(__file__).resolve().parents[1] / "config/config.example.toml"
        self.config.write_text(source.read_text())
        self.secrets = FakeSecrets()
        self.onboarding = OnboardingManager(self.config, self.secrets, self.state)
        self.opened = []
        self.now = [1000.0]
        self.handler = FakeHandler()
        self.login = LoginManager(
            self.onboarding, self.state.parent, handler=self.handler,
            open_url=self.opened.append, clock=lambda: self.now[0],
        )
        patcher = patch("zulip_hub.browser_login.ZulipClient.server_settings", return_value=SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)
        tester = patch("zulip_hub.onboarding.ZulipClient.test_connection", return_value={})
        tester.start()
        self.addCleanup(tester.stop)
        notify = patch.object(LoginManager, "notify")
        notify.start()
        self.addCleanup(notify.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def redirect(self, otp, realm="https://fstar.zulipchat.com", email="me@example.org"):
        return "zulip://login?" + urlencode({
            "realm": realm, "email": email, "user_id": 5,
            "otp_encrypted_api_key": otp_encrypt_api_key(API_KEY, otp),
        })

    def started_otp(self):
        url = self.opened[-1]
        self.assertTrue(url.startswith("https://fstar.zulipchat.com/accounts/login/social/google?"))
        return parse_qs(urlsplit(url).query)["mobile_flow_otp"][0]

    def test_methods_lists_only_safe_relative_urls(self):
        result = self.login.methods({"site": "fstar.zulipchat.com"})
        self.assertEqual(result["site"], "https://fstar.zulipchat.com")
        self.assertEqual([m["name"] for m in result["methods"]], ["google"])
        self.assertTrue(result["password"])
        self.assertNotIn("login_url", json.dumps(result))

    def test_full_browser_flow_adds_account(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        self.assertTrue(self.handler.registered)
        otp = self.started_otp()
        self.assertEqual(len(otp), 64)
        pending = self.state.parent / "login-pending.json"
        self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
        self.login.finish(self.redirect(otp))
        self.assertEqual(self.secrets.values, {("https://fstar.zulipchat.com", "me@example.org"): API_KEY})
        self.assertFalse(pending.exists())
        self.assertEqual(self.handler.restored, "org.zulip.Zulip.desktop")
        accounts = self.onboarding.status()["accounts"]
        self.assertEqual([(a["site"], a["name"]) for a in accounts],
                         [("https://fstar.zulipchat.com", "FStar")])
        status = self.login.status()
        self.assertFalse(status["pending"])
        self.assertTrue(status["result"]["ok"])
        self.assertIsNone(self.login.status()["result"])

    def test_unknown_method_is_refused(self):
        with self.assertRaises(LoginError):
            self.login.start({"site": "https://fstar.zulipchat.com", "method": "evil"})
        self.assertEqual(self.opened, [])

    def test_redirect_from_other_server_is_refused(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        otp = self.started_otp()
        result = self.login.finish(self.redirect(otp, realm="https://evil.example.com"))
        self.assertFalse(result["ok"])
        self.assertEqual(self.secrets.values, {})
        self.assertEqual(self.handler.restored, "org.zulip.Zulip.desktop")

    def test_finish_without_pending_login_is_refused(self):
        result = self.login.finish(self.redirect("0f" * 32))
        self.assertFalse(result["ok"])
        self.assertEqual(self.secrets.values, {})

    def test_expired_login_is_refused(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        otp = self.started_otp()
        self.now[0] += 3600
        result = self.login.finish(self.redirect(otp))
        self.assertFalse(result["ok"])
        self.assertEqual(self.secrets.values, {})

    def test_status_expires_abandoned_login(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        self.now[0] += 3600
        status = self.login.status()
        self.assertFalse(status["pending"])
        self.assertFalse(status["result"]["ok"])
        self.assertEqual(self.handler.restored, "org.zulip.Zulip.desktop")

    def test_cancel_restores_handler(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        self.login.cancel()
        self.assertFalse(self.login.status()["pending"])
        self.assertEqual(self.handler.restored, "org.zulip.Zulip.desktop")

    def test_restarting_login_keeps_original_previous_handler(self):
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        self.handler.previous = HANDLER_DESKTOP  # déjà le nôtre
        self.login.start({"site": "https://fstar.zulipchat.com", "method": "google"})
        self.login.cancel()
        self.assertEqual(self.handler.restored, "org.zulip.Zulip.desktop")

    def test_password_login(self):
        with patch("zulip_hub.browser_login.ZulipClient.fetch_api_key",
                   return_value={"api_key": API_KEY, "email": "me@mpi-sp.org"}) as fetch:
            result = self.login.password({
                "site": "https://zulip.mpi-sp.org", "email": "me@mpi-sp.org", "password": "pw",
            })
        fetch.assert_called_once_with("me@mpi-sp.org", "pw")
        self.assertTrue(result["configured"])
        self.assertNotIn("pw", self.config.read_text())
        self.assertEqual(self.secrets.values[("https://zulip.mpi-sp.org", "me@mpi-sp.org")], API_KEY)

    def test_import_zuliprc_files_and_delete_them(self):
        downloads = Path(self.temporary.name) / "Downloads"
        downloads.mkdir()
        (downloads / "zuliprc").write_text(
            f"[api]\nemail=me@example.org\nkey={API_KEY}\nsite=https://fstar.zulipchat.com\n")
        (downloads / "zuliprc (1)").write_text(
            f"[api]\nemail=me@mpi-sp.org\nkey={API_KEY}\nsite=https://zulip.mpi-sp.org\n")
        (downloads / "zuliprc (2)").write_text("not ini at all")
        result = self.login.import_zuliprc({"directory": str(downloads)})
        self.assertEqual(len(result["accounts"]), 2)
        self.assertIn("valide", result["message"])
        self.assertEqual(sorted(p.name for p in downloads.iterdir()), ["zuliprc (2)"])

    def test_protocol_routes_login_actions(self):
        from io import StringIO
        output = StringIO()
        with patch("zulip_hub.onboarding.Paths.defaults") as defaults:
            defaults.return_value.state = self.state
            serve_once(self.config, StringIO(json.dumps(
                {"action": "login_methods", "site": "fstar.zulipchat.com"}) + "\n"), output)
        self.assertEqual(json.loads(output.getvalue())["methods"][0]["name"], "google")


class SchemeHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.handler = SchemeHandler(root / "applications", root / "mimeapps.list")
        self.calls = []

        def run(argv, **_kwargs):
            self.calls.append(argv)
            if argv[:2] == ["xdg-mime", "query"]:
                return CommandResult(0, self.current + "\n")
            return CommandResult(0, "")

        self.current = ""
        patcher = patch("zulip_hub.browser_login.commands.run", side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)
        available = patch("zulip_hub.browser_login.commands.available", return_value=True)
        available.start()
        self.addCleanup(available.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def test_register_writes_desktop_entry_and_sets_default(self):
        with patch.dict("os.environ", {"ZULIP_HUB_RUNNER": "/home/u/plug in/run-plugin.py"}):
            self.assertEqual(self.handler.register(), "")
        entry = self.handler.desktop_path.read_text()
        self.assertIn('Exec=/usr/bin/python3 "/home/u/plug in/run-plugin.py" finish-login %u', entry)
        self.assertIn("NoDisplay=true", entry)
        self.assertIn(["xdg-mime", "default", HANDLER_DESKTOP, "x-scheme-handler/zulip"], self.calls)
        # Sans cache à jour, le portail du navigateur ne trouve pas le gestionnaire.
        self.assertEqual(self.calls[-1], ["update-desktop-database", str(self.handler.applications)])

    def test_restore_previous_handler(self):
        self.current = "org.zulip.Zulip.desktop"
        previous = self.handler.register()
        self.handler.restore(previous)
        self.assertFalse(self.handler.desktop_path.exists())
        self.assertEqual(self.calls[-2],
                         ["xdg-mime", "default", "org.zulip.Zulip.desktop", "x-scheme-handler/zulip"])
        self.assertEqual(self.calls[-1][0], "update-desktop-database")

    def test_restore_without_previous_forgets_association(self):
        self.handler.mimeapps.write_text(
            "[Default Applications]\n"
            f"x-scheme-handler/zulip={HANDLER_DESKTOP}\n"
            "text/html=chromium.desktop\n"
        )
        self.handler.register()
        self.handler.restore("")
        text = self.handler.mimeapps.read_text()
        self.assertNotIn("zulip", text)
        self.assertIn("text/html=chromium.desktop", text)

    def test_symlinked_mimeapps_is_left_alone(self):
        target = Path(self.temporary.name) / "dotfiles-mimeapps"
        target.write_text(f"x-scheme-handler/zulip={HANDLER_DESKTOP}\n")
        self.handler.mimeapps.symlink_to(target)
        self.handler.restore("")
        self.assertIn("zulip", target.read_text())
        self.assertTrue(self.handler.mimeapps.is_symlink())


if __name__ == "__main__":
    unittest.main()
