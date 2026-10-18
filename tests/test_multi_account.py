import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from zulip_hub.cli import main
from zulip_hub.composer import ComposerError, ComposerManager
from zulip_hub.config import ConfigError, default_account_name, load_config
from zulip_hub.daemon import BridgeDaemon, MultiBridge
from zulip_hub.onboarding import OnboardingManager
from zulip_hub.reader import ReaderManager
from zulip_hub.state import HubState, MergedStateStore, StateReducer, StateStore, recent_row


TWO_ACCOUNTS = """
[[accounts]]
name = "FStar"
site = "https://fstar.zulipchat.com/"
email = "me@example.org"

[[accounts]]
site = "https://zulip.mpi-sp.org"
email = "me@mpi-sp.org"

[notifications]
enabled = true
"""

TWO_SITE_STATE = {
    "schema_version": 1,
    "recent": [
        {
            "id": 42, "type": "stream", "sender": "Alice", "channel": "general",
            "topic": "fstar", "stream_id": 3, "timestamp": 200, "flags": [],
            "site": "https://fstar.zulipchat.com", "account": "FStar",
        },
        {
            "id": 42, "type": "private", "sender": "Bob", "sender_id": 8,
            "recipient_ids": [1, 8], "timestamp": 100, "flags": [],
            "site": "https://zulip.mpi-sp.org", "account": "mpi-sp.org",
        },
    ],
    "accounts": [
        {"site": "https://fstar.zulipchat.com", "max_message_length": 500},
        {"site": "https://zulip.mpi-sp.org", "max_message_length": 50},
    ],
}


class FakeSecrets:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, site, email):
        from zulip_hub.secrets import SecretError
        try:
            return self.values[(site, email)]
        except KeyError as exc:
            raise SecretError("clé API Zulip absente du trousseau") from exc

    def store(self, site, email, key):
        self.values[(site, email)] = key

    def delete(self, site, email):
        self.values.pop((site, email), None)


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = root / "config.toml"
        self.state = root / "state.json"

    def tearDown(self):
        self.temporary.cleanup()


class ConfigTests(TemporaryFiles):
    def test_accounts_array_is_loaded_in_order(self):
        self.config.write_text(TWO_ACCOUNTS)
        config = load_config(self.config)
        self.assertEqual(
            [account.site for account in config.accounts],
            ["https://fstar.zulipchat.com", "https://zulip.mpi-sp.org"],
        )
        self.assertEqual(config.account.site, "https://fstar.zulipchat.com")
        self.assertEqual([account.label for account in config.accounts], ["FStar", "mpi-sp.org"])

    def test_legacy_single_account_still_loads(self):
        self.config.write_text('[account]\nsite = "https://chat.example.org"\nemail = "a@b.org"\n')
        config = load_config(self.config)
        self.assertEqual(len(config.accounts), 1)
        self.assertEqual(config.account.email, "a@b.org")

    def test_both_forms_together_are_rejected(self):
        self.config.write_text(
            '[account]\nsite = "https://a.org"\nemail = "a@a.org"\n' + TWO_ACCOUNTS
        )
        with self.assertRaises(ConfigError):
            load_config(self.config)

    def test_duplicate_site_is_rejected(self):
        self.config.write_text(
            '[[accounts]]\nsite = "https://a.org"\nemail = "a@a.org"\n'
            '[[accounts]]\nsite = "https://a.org/"\nemail = "b@a.org"\n'
        )
        with self.assertRaises(ConfigError):
            load_config(self.config)

    def test_key_in_any_account_is_rejected(self):
        self.config.write_text(
            '[[accounts]]\nsite = "https://a.org"\nemail = "a@a.org"\napi_key = "x"\n'
        )
        with self.assertRaises(ConfigError):
            load_config(self.config)

    def test_account_for_rejects_unknown_site(self):
        self.config.write_text(TWO_ACCOUNTS)
        config = load_config(self.config)
        self.assertEqual(config.account_for("https://zulip.mpi-sp.org/").email, "me@mpi-sp.org")
        self.assertEqual(config.account_for(None).site, "https://fstar.zulipchat.com")
        with self.assertRaises(ConfigError):
            config.account_for("https://evil.example.com")

    def test_default_names(self):
        self.assertEqual(default_account_name("https://aeneas-verif.zulipchat.com"), "aeneas-verif")
        self.assertEqual(default_account_name("https://zulip.mpi-sp.org"), "mpi-sp.org")
        self.assertEqual(default_account_name("https://chat.example.org"), "chat.example.org")


class MergedStateTests(TemporaryFiles):
    def merged(self):
        return MergedStateStore(
            StateStore(self.state),
            [("https://a.org", "A"), ("https://b.org", "B")],
            limit=3,
        )

    def test_counts_are_summed_and_rows_tagged_and_sorted(self):
        merged = self.merged()
        first = HubState(connected=True, unread={"total": 2, "mentions": 1, "private": 0})
        first.recent = [{"id": 1, "timestamp": 10}, {"id": 2, "timestamp": 30}]
        second = HubState(connected=False, error="clé révoquée",
                          unread={"total": 3, "mentions": 0, "private": 2})
        second.recent = [{"id": 1, "timestamp": 20}, {"id": 5, "timestamp": 5}]
        merged.slot(0).write(first)
        merged.slot(1).write(second)
        state = StateStore(self.state).read()
        self.assertEqual(state["unread"], {"total": 5, "mentions": 1, "private": 2})
        self.assertEqual(
            [(row["site"], row["id"]) for row in state["recent"]],
            [("https://a.org", 2), ("https://b.org", 1), ("https://a.org", 1)],
        )
        self.assertTrue(state["connected"])
        self.assertEqual(state["error"], "B : clé révoquée")
        self.assertEqual([entry["name"] for entry in state["accounts"]], ["A", "B"])
        self.assertFalse(state["accounts"][1]["connected"])

    def test_single_account_error_is_not_prefixed(self):
        merged = MergedStateStore(StateStore(self.state), [("https://a.org", "A")])
        merged.slot(0).write(HubState(error="hors ligne"))
        self.assertEqual(StateStore(self.state).read()["error"], "hors ligne")

    def test_recent_row_distinguishes_servers(self):
        self.assertEqual(recent_row(TWO_SITE_STATE, 42, "https://zulip.mpi-sp.org")["sender"], "Bob")
        self.assertEqual(recent_row(TWO_SITE_STATE, 42)["sender"], "Alice")
        self.assertIsNone(recent_row(TWO_SITE_STATE, 42, "https://evil.example.com"))


class FakeClient:
    def __init__(self, site, message_id):
        self.site = site
        self.message_id = message_id
        self.served = threading.Event()

    def register(self):
        return {"queue_id": "q", "last_event_id": -1, "unread_msgs": {"pms": [self.message_id]}}

    def events(self, queue_id, last_event_id):
        self.served.set()
        threading.Event().wait(0.05)
        return []


class MultiBridgeTests(TemporaryFiles):
    def test_each_account_runs_and_stop_ends_all(self):
        merged = MergedStateStore(
            StateStore(self.state), [("https://a.org", "A"), ("https://b.org", "B")],
        )
        stop = threading.Event()
        clients = [FakeClient("https://a.org", 1), FakeClient("https://b.org", 2)]
        daemons = [
            BridgeDaemon(client, merged.slot(index), StateReducer(), sleep=stop.wait,
                         install_signals=False)
            for index, client in enumerate(clients)
        ]
        bridge = MultiBridge(daemons, stop, on_stop=merged.mark_stopped)
        with patch("zulip_hub.daemon.signal.signal"):
            runner = threading.Thread(target=bridge.run)
            runner.start()
            for client in clients:
                self.assertTrue(client.served.wait(2))
            bridge.stop()
            runner.join(5)
        self.assertFalse(runner.is_alive())
        state = StateStore(self.state).read()
        self.assertEqual(state["unread"]["private"], 2)
        self.assertFalse(state["connected"])


class OnboardingTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        source = Path(__file__).resolve().parents[1] / "config/config.example.toml"
        self.config.write_text(source.read_text())
        self.secrets = FakeSecrets()
        self.manager = OnboardingManager(self.config, self.secrets, self.state)

    def add(self, site, email, key="k", **extra):
        with patch("zulip_hub.onboarding.ZulipClient.test_connection", return_value={}):
            return self.manager.setup({"site": site, "email": email, "api_key": key, **extra})

    def test_first_setup_replaces_example_and_second_appends(self):
        self.add("https://fstar.zulipchat.com", "me@example.org", name="FStar")
        result = self.add("https://zulip.mpi-sp.org", "me@mpi-sp.org")
        self.assertEqual(
            [account["site"] for account in result["accounts"]],
            ["https://fstar.zulipchat.com", "https://zulip.mpi-sp.org"],
        )
        text = self.config.read_text()
        self.assertNotIn("[account]\n", text)
        self.assertNotIn("example.com", text.split("[bridge]")[0])
        self.assertEqual(text.count("[[accounts]]"), 2)
        self.assertIn("[notifications]", text)
        config = load_config(self.config)
        self.assertEqual(config.accounts[0].name, "FStar")

    def test_setup_with_same_site_updates_in_place(self):
        self.add("https://a.example.org", "old@example.org", key="old")
        self.add("https://b.example.org", "me@example.org")
        result = self.add("https://a.example.org", "new@example.org", key="new")
        self.assertEqual(len(result["accounts"]), 2)
        self.assertEqual(result["accounts"][0]["email"], "new@example.org")
        self.assertNotIn(("https://a.example.org", "old@example.org"), self.secrets.values)

    def test_edit_can_move_account_to_new_site(self):
        self.add("https://a.example.org", "me@example.org")
        result = self.add(
            "https://c.example.org", "me@example.org",
            original_site="https://a.example.org",
        )
        self.assertEqual([a["site"] for a in result["accounts"]], ["https://c.example.org"])
        self.assertEqual(list(self.secrets.values), [("https://c.example.org", "me@example.org")])

    def test_remove_account_drops_config_and_key(self):
        self.add("https://a.example.org", "me@example.org")
        self.add("https://b.example.org", "me@example.org")
        result = self.manager.handle({"action": "remove_account", "site": "https://a.example.org"})
        self.assertEqual([a["site"] for a in result["accounts"]], ["https://b.example.org"])
        self.assertEqual(list(self.secrets.values), [("https://b.example.org", "me@example.org")])
        self.assertTrue(result["configured"])

    def test_removing_last_account_disconnects(self):
        self.add("https://a.example.org", "me@example.org")
        result = self.manager.handle({"action": "remove_account", "site": "https://a.example.org"})
        self.assertFalse(result["configured"])
        self.assertFalse(result["active"])

    def test_configured_when_any_account_has_key(self):
        self.add("https://a.example.org", "me@example.org")
        self.add("https://b.example.org", "me@example.org")
        self.secrets.delete("https://a.example.org", "me@example.org")
        status = self.manager.status()
        self.assertTrue(status["configured"])
        self.assertEqual([a["has_secret"] for a in status["accounts"]], [False, True])

    def test_diagnostics_lists_accounts(self):
        self.state.write_text(json.dumps({"accounts": [
            {"site": "https://a.example.org", "name": "A", "connected": True},
            {"site": "https://b.example.org", "name": "B", "error": "hors ligne"},
        ]}))
        accounts = self.manager.diagnostics()["accounts"]
        self.assertEqual([(a["name"], a["connected"], a["error"]) for a in accounts],
                         [("A", True, ""), ("B", False, "hors ligne")])


class RoutingTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.config.write_text(TWO_ACCOUNTS)
        self.state.write_text(json.dumps(TWO_SITE_STATE))
        self.secrets = FakeSecrets({
            ("https://fstar.zulipchat.com", "me@example.org"): "fstar-key",
            ("https://zulip.mpi-sp.org", "me@mpi-sp.org"): "mpi-key",
        })

    @patch("zulip_hub.reader.ZulipClient")
    def test_reader_fetches_from_the_rows_server(self, client_class):
        client_class.return_value.message.return_value = {"content": "Salut"}
        result = ReaderManager(self.config, self.state, self.secrets).read(
            {"message_id": 42, "site": "https://zulip.mpi-sp.org"}
        )
        self.assertEqual(client_class.call_args.args[:3],
                         ("https://zulip.mpi-sp.org", "me@mpi-sp.org", "mpi-key"))
        self.assertEqual(result["message"]["sender"], "Bob")
        self.assertEqual(result["message"]["site"], "https://zulip.mpi-sp.org")

    @patch("zulip_hub.composer.ZulipClient")
    def test_reply_goes_to_the_rows_server(self, client_class):
        client = client_class.return_value
        client.test_connection.return_value = {"user_id": 1}
        client.send_direct.return_value = 99
        ComposerManager(self.config, self.state, self.secrets).reply(
            {"message_id": 42, "site": "https://zulip.mpi-sp.org", "content": "ok"}
        )
        self.assertEqual(client_class.call_args.args[0], "https://zulip.mpi-sp.org")
        client.send_direct.assert_called_once_with([8], "ok")

    @patch("zulip_hub.composer.ZulipClient")
    def test_reply_uses_the_server_limit(self, client_class):
        with self.assertRaises(ComposerError):
            ComposerManager(self.config, self.state, self.secrets).reply(
                {"message_id": 42, "site": "https://zulip.mpi-sp.org", "content": "x" * 60}
            )
        client_class.assert_not_called()

    @patch("zulip_hub.composer.ZulipClient")
    def test_direct_message_to_unconfigured_site_is_refused(self, client_class):
        from zulip_hub.config import ConfigError as Refused
        with self.assertRaises(Refused):
            ComposerManager(self.config, self.state, self.secrets).send_direct(
                {"recipient_ids": [7], "content": "hi", "site": "https://evil.example.com"}
            )
        client_class.assert_not_called()

    @patch("zulip_hub.composer.ZulipClient")
    def test_directory_for_second_account(self, client_class):
        client = client_class.return_value
        client.test_connection.return_value = {"user_id": 1}
        client.users.return_value = []
        result = ComposerManager(self.config, self.state, self.secrets).directory(
            {"site": "https://zulip.mpi-sp.org"}
        )
        self.assertEqual(result["site"], "https://zulip.mpi-sp.org")
        self.assertEqual(result["recent_user_ids"], [8])
        self.assertEqual(result["max_message_length"], 50)

    @patch("zulip_hub.cli.UrlOpener")
    def test_open_url_accepts_any_configured_server(self, opener):
        code = main(["--config", str(self.config), "--state", str(self.state),
                     "open-url", "https://zulip.mpi-sp.org/#narrow/dm/8/near/1"])
        self.assertEqual(code, 0)
        code = main(["--config", str(self.config), "--state", str(self.state),
                     "open-url", "https://evil.example.com/#narrow/dm/8/near/1"])
        self.assertEqual(code, 2)

    @patch("zulip_hub.cli.UrlOpener")
    def test_open_message_uses_the_rows_server(self, opener):
        code = main(["--config", str(self.config), "--state", str(self.state),
                     "open-message", "42", "--site", "https://zulip.mpi-sp.org"])
        self.assertEqual(code, 0)
        url = opener.return_value.open.call_args.args[0]
        self.assertTrue(url.startswith("https://zulip.mpi-sp.org/#narrow/dm/"))

    def test_bridge_runs_accounts_with_keys_and_records_missing_ones(self):
        self.secrets.delete("https://zulip.mpi-sp.org", "me@mpi-sp.org")
        from zulip_hub import cli
        started = []
        with patch.object(cli.MultiBridge, "run", lambda bridge: started.extend(bridge.daemons)):
            cli.run_bridge(load_config(self.config), self.secrets, self.state)
        self.assertEqual([d.client.site for d in started], ["https://fstar.zulipchat.com"])
        state = StateStore(self.state).read()
        self.assertIn("absente", state["accounts"][1]["error"])


if __name__ == "__main__":
    unittest.main()
