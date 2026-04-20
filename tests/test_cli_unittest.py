import unittest
from unittest.mock import MagicMock, patch

from bekusup import cli


class TestCliConvenienceFlow(unittest.TestCase):
    @patch("builtins.print")
    @patch("bekusup.cli.cmd_run")
    @patch("bekusup.cli.load_config")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_defaults_to_run_when_config_exists(
        self, mock_exists, mock_load_config, mock_cmd_run, mock_print
    ):
        mock_exists.return_value = True
        mock_load_config.return_value = MagicMock()

        rc = cli.main([])

        self.assertEqual(rc, 0)
        mock_load_config.assert_called_once_with("config.yaml")
        mock_cmd_run.assert_called_once()
        args_used = mock_cmd_run.call_args[0][0]
        self.assertTrue(cli.resolve_dry_run(args_used))

    @patch("builtins.print")
    @patch("bekusup.cli.run_init_wizard")
    @patch("builtins.input", return_value="1")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_routes_to_init_menu_when_config_missing(
        self, mock_exists, mock_input, mock_run_init, mock_print
    ):
        mock_exists.return_value = False
        mock_run_init.return_value = 0

        rc = cli.main([])

        self.assertEqual(rc, 0)
        mock_run_init.assert_called_once_with("config.yaml")

    @patch("builtins.print")
    @patch("bekusup.transports.get_provider")
    @patch("bekusup.scanner.get_verified_targets")
    @patch("bekusup.store.IndexStore")
    @patch("bekusup.session.SessionManager")
    def test_cmd_run_uses_dry_run_by_default(
        self, mock_session_mgr, mock_index_cls, mock_get_targets, mock_get_provider, mock_print
    ):
        config = MagicMock()
        config.hosts = [MagicMock()]
        config.hosts[0].name = "localhost"
        config.hosts[0].uri = None
        path_cfg = MagicMock()
        path_cfg.source = "/tmp/source"
        path_cfg.dest_subdir = "data"
        config.hosts[0].paths = [path_cfg]
        config.run_policy.max_parallel_hosts = 1

        mock_get_targets.return_value = [({"name": "sdb"}, "/mnt/backup", "serial1", "uuid1", "backup")]
        mock_index_cls.return_value.get_drive.return_value = None

        session = MagicMock()
        session.begin_session.return_value = True
        session.snapshot_base = None
        session.sessions_dir = "/mnt/backup/sessions"
        session.timestamp = "2026-01-01T00-00-00"
        session.manifest = {"outcome": "complete", "hosts": {"localhost": {"status": "succeeded"}}}
        session.get_host_dest_dir.return_value = "/mnt/backup/sessions/2026-01-01T00-00-00/localhost"
        mock_session_mgr.return_value = session

        provider = MagicMock()
        provider.sync.return_value = True
        mock_get_provider.return_value = provider

        args = MagicMock()
        args.dry_run = False
        args.no_dry_run = False

        cli.cmd_run(args, config)

        _, kwargs = mock_session_mgr.call_args
        self.assertTrue(kwargs["dry_run"])
        self.assertTrue(provider.sync.call_args.kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
