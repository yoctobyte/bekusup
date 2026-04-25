import unittest
from unittest.mock import MagicMock, patch

from bekusup import cli


class TestCliConvenienceFlow(unittest.TestCase):
    @patch("builtins.print")
    @patch("bekusup.cli.cmd_run")
    @patch("bekusup.cli.load_config")
    @patch("builtins.input", return_value="")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_menu_defaults_to_run_when_config_exists(
        self, mock_exists, mock_input, mock_load_config, mock_cmd_run, mock_print
    ):
        mock_exists.return_value = True
        config = MagicMock()
        config.run_policy.run_without_command = False
        mock_load_config.return_value = config

        rc = cli.main([])

        self.assertEqual(rc, 0)
        self.assertGreaterEqual(mock_load_config.call_count, 1)
        mock_cmd_run.assert_called_once()
        args_used = mock_cmd_run.call_args[0][0]
        self.assertTrue(cli.resolve_dry_run(args_used))

    @patch("builtins.print")
    @patch("bekusup.cli.cmd_run")
    @patch("bekusup.cli.load_config")
    @patch("builtins.input")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_can_auto_run_when_config_says_so(
        self, mock_exists, mock_input, mock_load_config, mock_cmd_run, mock_print
    ):
        mock_exists.return_value = True
        config = MagicMock()
        config.run_policy.run_without_command = True
        mock_load_config.return_value = config

        rc = cli.main([])

        self.assertEqual(rc, 0)
        mock_input.assert_not_called()
        mock_cmd_run.assert_called_once()

    @patch("builtins.print")
    @patch("bekusup.cli.run_config_wizard")
    @patch("builtins.input", return_value="")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_routes_to_init_menu_when_config_missing(
        self, mock_exists, mock_input, mock_run_wizard, mock_print
    ):
        mock_exists.return_value = False
        mock_run_wizard.return_value = 0

        rc = cli.main([])

        self.assertEqual(rc, 0)
        mock_run_wizard.assert_called_once_with("config.yaml", allow_overwrite=False)

    @patch("builtins.print")
    @patch("bekusup.cli.run_config_wizard")
    @patch("bekusup.cli.load_config")
    @patch("builtins.input", return_value="4")
    @patch("bekusup.cli.os.path.exists")
    def test_main_without_args_can_reconfigure_existing_config(
        self, mock_exists, mock_input, mock_load_config, mock_wizard, mock_print
    ):
        mock_exists.return_value = True
        config = MagicMock()
        config.run_policy.run_without_command = False
        mock_load_config.return_value = config
        mock_wizard.return_value = 0

        rc = cli.main([])

        self.assertEqual(rc, 0)
        mock_wizard.assert_called_once_with("config.yaml", allow_overwrite=True)

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

        mock_get_targets.return_value = [
            ({"name": "sdb"}, "/mnt/backup", "serial1", "uuid1", "backup", False)
        ]
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

    @patch("builtins.print")
    @patch("bekusup.scanner.unmount_if_mounted_by_bekusup")
    @patch("bekusup.transports.get_provider")
    @patch("bekusup.scanner.get_verified_targets")
    @patch("bekusup.store.IndexStore")
    @patch("bekusup.session.SessionManager")
    def test_cmd_run_dry_run_unmounts_temporary_mount(
        self,
        mock_session_mgr,
        mock_index_cls,
        mock_get_targets,
        mock_get_provider,
        mock_unmount,
        mock_print,
    ):
        config = MagicMock()
        config.hosts = []
        config.run_policy.max_parallel_hosts = 1
        mock_get_targets.return_value = [
            ({"name": "sdb"}, "/mnt/backup", "serial1", "uuid1", "backup", True)
        ]
        mock_index_cls.return_value.get_drive.return_value = None

        session = MagicMock()
        session.begin_session.return_value = True
        session.snapshot_base = None
        session.sessions_dir = "/mnt/backup/sessions"
        session.timestamp = "2026-01-01T00-00-00"
        session.manifest = {"outcome": "complete", "hosts": {}}
        mock_session_mgr.return_value = session

        args = MagicMock()
        args.dry_run = False
        args.no_dry_run = False

        cli.cmd_run(args, config)

        mock_get_targets.assert_called_once()
        self.assertTrue(mock_get_targets.call_args.kwargs["allow_mount"])
        mock_unmount.assert_called_once_with("/mnt/backup")

    @patch("builtins.print")
    @patch("bekusup.cli.write_yaml_atomic")
    @patch("builtins.input", side_effect=["", "", "y"])
    @patch("bekusup.cli.os.path.exists", return_value=False)
    def test_run_config_wizard_can_enable_run_without_command(
        self, mock_exists, mock_input, mock_write_yaml, mock_print
    ):
        rc = cli.run_config_wizard("config.yaml", allow_overwrite=False)

        self.assertEqual(rc, 0)
        written = mock_write_yaml.call_args[0][1]
        self.assertTrue(written["run_policy"]["run_without_command"])


if __name__ == "__main__":
    unittest.main()
