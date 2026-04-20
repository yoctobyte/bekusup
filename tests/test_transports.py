import unittest
from unittest.mock import patch
from bekusup.transports import RsyncProvider
from bekusup.config import HostConfig

class TestRsyncProvider(unittest.TestCase):
    @patch('builtins.print')
    @patch('bekusup.transports.subprocess.run')
    @patch('bekusup.transports.os.makedirs')
    def test_rsync_sync_sshpass(self, mock_dirs, mock_run, mock_print):
        host = HostConfig(name="test", transport="ssh", paths=[], uri="ssh://user:pass@10.0.0.1")
        provider = RsyncProvider(host)
        
        # Simulate local network sync with NO snapshot
        provider.sync("/remote/src", "/local/dest")
        
        cmd_called = mock_run.call_args[0][0]
        self.assertIn("sshpass", cmd_called)
        self.assertIn("pass", cmd_called)
        self.assertIn("rsync", cmd_called)
        self.assertIn("user@10.0.0.1:/remote/src/", cmd_called)

    @patch('builtins.print')
    @patch('bekusup.transports.os.stat')
    @patch('bekusup.transports.os.path.exists')
    @patch('bekusup.transports.subprocess.run')
    @patch('bekusup.transports.os.makedirs')
    def test_rsync_sync_cross_drive_cache(self, mock_dirs, mock_run, mock_exists, mock_stat, mock_print):
        host = HostConfig(name="test", transport="ssh", paths=[], uri="ssh://10.0.0.1")
        provider = RsyncProvider(host)
        
        mock_exists.return_value = True
        
        # Mock st_dev to simulate DIFFERENT filesystems (cross-drive copy mapping)
        class MockStat:
            def __init__(self, dev):
                self.st_dev = dev
        
        def side_effect_stat(path):
            if path == "/snapshot/base":
                return MockStat(100)
            return MockStat(200)
            
        mock_stat.side_effect = side_effect_stat
        
        provider.sync("/remote/src", "/local/dest", snapshot_base="/snapshot/base")
        
        cmd_called = mock_run.call_args[0][0]
        
        # Ensure copy-dest was securely matched instead of link-dest
        self.assertTrue(any("--copy-dest=/snapshot/base" in arg for arg in cmd_called), "Did not inject copy-dest for cross-drive")

if __name__ == '__main__':
    unittest.main()
