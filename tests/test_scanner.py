import unittest
from unittest.mock import patch, MagicMock
import pytest
from bekusup.scanner import (
    ensure_mounted,
    is_system_device,
    resolve_target_disk,
    scan_candidate_disks,
    unmount_if_mounted_by_bekusup,
)

class TestScanner(unittest.TestCase):
    def test_is_system_device(self):
        # Should reject root
        root_node = {"mountpoints": ["/"]}
        self.assertTrue(is_system_device(root_node))
        
        # Should accept a safe backup path
        backup_node = {"mountpoints": ["/run/media/user/backup"]}
        self.assertFalse(is_system_device(backup_node))
        
        # Should reject if any nested child is a system path
        parent_node = {"children": [{"mountpoint": "/boot"}]}
        self.assertTrue(is_system_device(parent_node))

    @patch('bekusup.scanner.get_block_devices')
    def test_scan_candidate_disks(self, mock_get):
        mock_get.return_value = [
            {"name": "sda", "children": [{"name": "sda1", "mountpoints": ["/"]}]},
            {"name": "sdb", "label": "my_backup_1", "uuid": "123"},
            {"name": "sdc", "label": "ignore_me", "uuid": "456"},
        ]
        
        candidates = scan_candidate_disks("backup")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "sdb")

    @patch('bekusup.scanner.os.access', return_value=True)
    @patch('bekusup.scanner.os.path.exists', return_value=False)
    def test_ensure_mounted_existing_mount_returns_not_temporary(self, mock_exists, mock_access):
        mp, temporary = ensure_mounted(
            {"name": "sdb1", "mountpoints": ["/mnt/backup"]},
            "/mnt/bekusup",
        )

        self.assertEqual(mp, "/mnt/backup")
        self.assertFalse(temporary)

    @patch('bekusup.scanner.os.access', return_value=True)
    @patch('bekusup.scanner.os.path.exists', return_value=False)
    @patch('builtins.print')
    @patch('bekusup.scanner.subprocess.run')
    @patch('bekusup.scanner.os.makedirs')
    def test_ensure_mounted_can_mount_and_reports_temporary(self, mock_makedirs, mock_run, mock_print, mock_exists, mock_access):
        mp, temporary = ensure_mounted(
            {"name": "sdb1", "label": "backup_a", "mountpoints": []},
            "/mnt/bekusup",
            allow_mount=True,
        )

        self.assertEqual(mp, "/mnt/bekusup/backup_a")
        self.assertTrue(temporary)
        mock_run.assert_called_once_with(['sudo', 'mount', '/dev/sdb1', '/mnt/bekusup/backup_a'], check=True)

    @patch('bekusup.scanner.invoking_uid_gid', return_value=(1000, 1000))
    @patch('bekusup.scanner.sudo_available', return_value=True)
    @patch('bekusup.scanner.os.path.exists', return_value=False)
    @patch('bekusup.scanner.os.access', side_effect=[False, True])
    @patch('bekusup.scanner.subprocess.run')
    def test_ensure_mounted_existing_mount_chowns_when_needed(self, mock_run, mock_access, mock_exists, mock_sudo, mock_uid):
        mp, temporary = ensure_mounted(
            {"name": "sdb1", "mountpoints": ["/mnt/backup"]},
            "/mnt/bekusup",
        )

        self.assertEqual(mp, "/mnt/backup")
        self.assertFalse(temporary)
        self.assertEqual(mock_run.call_args_list[0].args[0], ['sudo', 'chown', '1000:1000', '/mnt/backup'])

    @patch('bekusup.scanner.invoking_uid_gid', return_value=(1000, 1000))
    @patch('bekusup.scanner.os.path.exists', return_value=True)
    @patch('bekusup.scanner.os.access', side_effect=[True, False])
    @patch('bekusup.scanner.subprocess.run')
    def test_ensure_mounted_existing_mount_chowns_marker_when_dir_writable(
        self, mock_run, mock_access, mock_exists, mock_uid
    ):
        mp, temporary = ensure_mounted(
            {"name": "sdb1", "mountpoints": ["/mnt/backup"]},
            "/mnt/bekusup",
        )

        self.assertEqual(mp, "/mnt/backup")
        self.assertFalse(temporary)
        mock_run.assert_called_once_with(
            ['sudo', 'chown', '1000:1000', '/mnt/backup/.bekusup-volume.json'],
            check=True,
        )

    @patch('builtins.print')
    @patch('bekusup.scanner.subprocess.run')
    def test_unmount_if_mounted_by_bekusup_calls_umount(self, mock_run, mock_print):
        unmount_if_mounted_by_bekusup("/mnt/bekusup/backup_a")

        mock_run.assert_called_once_with(['sudo', '-n', 'umount', '/mnt/bekusup/backup_a'], check=True)

if __name__ == '__main__':
    unittest.main()


def _cfg(label="backup"):
    cfg = MagicMock()
    cfg.destination.label_contains = label
    return cfg


@patch('bekusup.scanner.scan_candidate_disks')
def test_resolve_target_disk_single(mock_scan):
    mock_scan.return_value = [{"name": "sdb", "label": "my_backup"}]
    disk = resolve_target_disk(_cfg())
    assert disk["name"] == "sdb"


@patch('bekusup.scanner.scan_candidate_disks')
def test_resolve_target_disk_none(mock_scan):
    mock_scan.return_value = []
    with pytest.raises(SystemExit):
        resolve_target_disk(_cfg())


@patch('bekusup.scanner.scan_candidate_disks')
def test_resolve_target_disk_multiple(mock_scan):
    mock_scan.return_value = [
        {"name": "sdb", "label": "backup_a"},
        {"name": "sdc", "label": "backup_b"},
    ]
    with pytest.raises(SystemExit):
        resolve_target_disk(_cfg())


@patch('bekusup.scanner.scan_candidate_disks')
def test_resolve_target_disk_explicit_device(mock_scan):
    mock_scan.return_value = [
        {"name": "sdb1", "label": "backup_a"},
        {"name": "sdc1", "label": "backup_b"},
    ]
    disk = resolve_target_disk(_cfg(), target_device="/dev/sdb1")
    assert disk["name"] == "sdb1"


@patch('bekusup.scanner.scan_candidate_disks')
def test_resolve_target_disk_explicit_device_must_be_eligible(mock_scan):
    mock_scan.return_value = [
        {"name": "sdb1", "label": "backup_a"},
        {"name": "sdc1", "label": "backup_b"},
    ]
    with pytest.raises(SystemExit):
        resolve_target_disk(_cfg(), target_device="/dev/sdd1")
