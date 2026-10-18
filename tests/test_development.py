from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zulip_hub.cli import main
from zulip_hub.config import DevelopmentConfig, load_config
from zulip_hub.development import AgentError, PLUGIN_ROOT, agent_command, start_agent


class DevelopmentTests(unittest.TestCase):
    def test_defaults_are_off_and_point_at_plugin(self):
        config = DevelopmentConfig()
        self.assertFalse(config.enabled)
        command = agent_command(config)
        self.assertEqual(command[:4], ["omarchy-launch-tui", "--app-id=org.omarchy.zulip-hub-agent", "bash", "-lc"])
        self.assertEqual(command[6:], [str(PLUGIN_ROOT), "claude"])

    def test_directory_and_command_never_enter_the_script(self):
        with tempfile.TemporaryDirectory() as directory:
            weird = Path(directory) / '$(touch pwned); "x'
            weird.mkdir()
            command = agent_command(DevelopmentConfig(True, str(weird), ("claude", "--model", "x;y")))
        self.assertEqual(command[4], 'cd -- "$1" && shift && exec "$@"')
        self.assertEqual(command[6:], [str(weird), "claude", "--model", "x;y"])

    def test_disabled_or_missing_directory_is_refused(self):
        with self.assertRaises(AgentError):
            start_agent(DevelopmentConfig(False))
        with self.assertRaises(AgentError):
            start_agent(DevelopmentConfig(True, "/nonexistent/zulip-hub"))

    @patch("zulip_hub.development.commands.spawn")
    def test_start_spawns_terminal(self, spawn):
        start_agent(DevelopmentConfig(True))
        self.assertEqual(spawn.call_args.args[0][0], "omarchy-launch-tui")

    def test_config_section(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[account]\nsite = "https://a.org"\nemail = "a@a.org"\n'
                '[development]\nenabled = true\ndirectory = "~/src/hub"\nagent_command = ["claude", "-c"]\n'
            )
            config = load_config(path).development
            self.assertEqual((config.enabled, config.directory, config.agent_command),
                             (True, "~/src/hub", ("claude", "-c")))
            path.write_text(
                '[account]\nsite = "https://a.org"\nemail = "a@a.org"\n[development]\nagent_command = "claude"\n'
            )
            self.assertEqual(main(["--config", str(path), "check-config"]), 2)


if __name__ == "__main__":
    unittest.main()
