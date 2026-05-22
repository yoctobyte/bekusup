import unittest
from unittest.mock import MagicMock, patch

from bekusup.tui import BekusupTUI


class TestBekusupTUI(unittest.TestCase):
    @patch("builtins.print")
    @patch("bekusup.tui.sys.stdout")
    def test_run_non_tty_returns_without_name_error(self, mock_stdout, mock_print):
        mock_stdout.isatty.return_value = False

        app = BekusupTUI("missing-config.yaml")
        app.run()

        mock_print.assert_called_once_with("Not a TTY. TUI cannot run.")

    @patch("bekusup.tui.socket.gethostname", return_value="host1")
    @patch.dict("bekusup.tui.os.environ", {"USER": "rene"}, clear=True)
    def test_add_host_flow_asks_for_single_ssh_target(self, mock_hostname):
        app = BekusupTUI("missing-config.yaml")
        app._get_input = MagicMock(return_value="alice@example.lan")
        app._verify_ssh_target = MagicMock(return_value=True)

        app._add_host_flow()

        app._get_input.assert_called_once_with("SSH target", "root@host1")
        app._verify_ssh_target.assert_called_once_with("alice@example.lan")
        self.assertEqual(
            app.config_data["hosts"],
            [
                {
                    "name": "example.lan",
                    "transport": "ssh",
                    "uri": "ssh://alice@example.lan",
                    "paths": [{"source": "/home/alice", "dest_subdir": "."}],
                }
            ],
        )
        self.assertEqual(app.selected_index, 0)

    def test_split_ssh_target_accepts_password_form(self):
        app = BekusupTUI("missing-config.yaml")

        user, name, raw = app._split_ssh_target("ssh://alice:secret@example.lan")

        self.assertEqual(user, "alice")
        self.assertEqual(name, "example.lan")
        self.assertEqual(raw, "alice:secret@example.lan")

    def test_add_host_flow_offers_key_install_when_verify_fails(self):
        app = BekusupTUI("missing-config.yaml")
        app._get_input = MagicMock(side_effect=["alice@example.lan", "y"])
        app._verify_ssh_target = MagicMock(side_effect=[False, True])
        app._install_ssh_key_interactive = MagicMock(return_value=True)

        app._add_host_flow()

        app._install_ssh_key_interactive.assert_called_once_with("alice@example.lan")
        self.assertEqual(app._verify_ssh_target.call_count, 2)
        self.assertEqual(app.config_data["hosts"][0]["uri"], "ssh://alice@example.lan")


if __name__ == "__main__":
    unittest.main()
