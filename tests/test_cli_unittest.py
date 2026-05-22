import unittest
from unittest.mock import MagicMock, patch

from bekusup import cli


class TestCliConvenienceFlow(unittest.TestCase):
    def setUp(self):
        self.run_lock_patcher = patch("bekusup.lock.RunLock")
        self.run_lock_patcher.start()

    def tearDown(self):
        self.run_lock_patcher.stop()

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
        session.get_snapshot_base_for_host.return_value = None
        session.sessions_dir = "/mnt/backup"
        session.timestamp = "2026-01-01T00-00-00"
        session.manifest = {"outcome": "complete", "hosts": {"localhost": {"status": "succeeded"}}}
        session.final_host_dirs = {"localhost": "/mnt/backup/localhostT2026-01-01T00-00-00"}
        session.get_host_dest_dir.return_value = "/mnt/backup/localhostT2026-01-01T00-00-00.incomplete"
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
        session.get_snapshot_base_for_host.return_value = None
        session.sessions_dir = "/mnt/backup"
        session.timestamp = "2026-01-01T00-00-00"
        session.manifest = {"outcome": "complete", "hosts": {}}
        session.final_host_dirs = {}
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

    @patch("builtins.print")
    @patch("bekusup.hardlink_candidates.resolve_link_mode", return_value="hardlink")
    @patch("bekusup.hardlink_candidates.find_hardlink_candidate_groups")
    @patch("bekusup.scanner.get_verified_targets")
    @patch("bekusup.store.IndexStore")
    def test_cmd_hardlink_candidates_reports_groups(
        self, mock_index_cls, mock_targets, mock_groups, mock_mode, mock_print
    ):
        mock_targets.return_value = [
            ({"name": "sdb1"}, "/mnt/backup", "serial1", "uuid1", "backup", False)
        ]
        mock_groups.return_value = [
            {
                "size": 10,
                "path_count": 2,
                "inode_count": 2,
                "potential_savings": 10,
                "paths": ["/mnt/backup/a", "/mnt/backup/b"],
            }
        ]
        args = MagicMock()
        args.limit = 20
        args.fix = False
        args.link_mode = "auto"
        config = MagicMock()

        cli.cmd_hardlink_candidates(args, config)

        mock_targets.assert_called_once()
        mock_groups.assert_called_once_with("/mnt/backup")
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("Found 1 candidate groups", printed)
        self.assertIn("Potential space savings: 10.0 B", printed)

    @patch("builtins.print")
    @patch("bekusup.hardlink_candidates.fix_candidate_groups")
    @patch("bekusup.hardlink_candidates.resolve_link_mode", return_value="reflink")
    @patch("bekusup.hardlink_candidates.find_hardlink_candidate_groups")
    @patch("bekusup.scanner.get_verified_targets")
    @patch("bekusup.store.IndexStore")
    def test_cmd_hardlink_candidates_fix_reports_result(
        self, mock_index_cls, mock_targets, mock_groups, mock_mode, mock_fix, mock_print
    ):
        mock_targets.return_value = [
            ({"name": "sdb1"}, "/mnt/backup", "serial1", "uuid1", "backup", False)
        ]
        mock_groups.return_value = [
            {
                "size": 10,
                "path_count": 2,
                "inode_count": 2,
                "potential_savings": 10,
                "paths": ["/mnt/backup/a", "/mnt/backup/b"],
            }
        ]
        mock_fix.return_value.changed = 1
        mock_fix.return_value.skipped = 0
        mock_fix.return_value.errors = 0
        mock_fix.return_value.bytes_shared = 10
        args = MagicMock()
        args.limit = 20
        args.fix = True
        args.link_mode = "auto"
        config = MagicMock()

        cli.cmd_hardlink_candidates(args, config)

        mock_fix.assert_called_once_with(mock_groups.return_value, "reflink")
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("Fix result: 1 paths changed", printed)


if __name__ == "__main__":
    unittest.main()
