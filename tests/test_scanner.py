import unittest
from unittest.mock import patch
from bekusup.scanner import is_system_device, scan_candidate_disks

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

if __name__ == '__main__':
    unittest.main()
