import pytest
from unittest.mock import patch, MagicMock
from bekusup.cli import cmd_run

@pytest.fixture
def mock_config():
    config = MagicMock()
    host = MagicMock()
    host.name = "testhost"
    host.uri = "ssh://user@10.0.0.1"
    host.paths = []
    config.hosts = [host]
    config.run_policy.max_parallel_hosts = 1
    return config

@patch('bekusup.transports.get_provider')
@patch('bekusup.scanner.get_verified_targets')
@patch('bekusup.store.IndexStore')
@patch('bekusup.session.SessionManager')
@patch('subprocess.run')
def test_cmd_run_host_unreachable(mock_sub_run, mock_session_mgr, mock_index, mock_get_targets, mock_get_provider, mock_config):
    # Simulate a drive being returned
    mock_get_targets.return_value = [ ({"name": "sda"}, "/mnt/sda", "111", "222", "L") ]
    
    session_instance = MagicMock()
    session_instance.begin_session.return_value = True
    session_instance.snapshot_base = None
    mock_session_mgr.return_value = session_instance
    
    import subprocess
    def side_effect(*args, **kwargs):
        if args[0][0] == "nc":
            raise subprocess.CalledProcessError(1, cmd=["nc"])
        return MagicMock(stdout='{"blockdevices": []}')
        
    mock_sub_run.side_effect = side_effect
    
    cmd_run(None, mock_config)
    
    session_instance.record_host_status.assert_called_with("testhost", "unreachable", "Failed 2-second reachability probe.")
    # provider never gets called
    mock_get_provider.assert_not_called()

@patch('bekusup.transports.get_provider')
@patch('bekusup.scanner.get_verified_targets')
@patch('bekusup.store.IndexStore')
@patch('bekusup.session.SessionManager')
@patch('subprocess.run')
def test_cmd_run_host_partial(mock_sub_run, mock_session_mgr, mock_index, mock_get_targets, mock_get_provider, mock_config):
    mock_get_targets.return_value = [ ({"name": "sda"}, "/mnt/sda", "111", "222", "L") ]
    
    session_instance = MagicMock()
    session_instance.begin_session.return_value = True
    session_instance.snapshot_base = None
    mock_session_mgr.return_value = session_instance
    
    # allow ping to succeed
    mock_sub_run.return_value = MagicMock()
    
    provider_mock = MagicMock()
    # Path 1 succeeds, Path 2 fails
    provider_mock.sync.side_effect = [True, False]
    mock_get_provider.return_value = provider_mock
    
    path1 = MagicMock(); path1.dest_subdir = "p1"; path1.source = "/src1"
    path2 = MagicMock(); path2.dest_subdir = "p2"; path2.source = "/src2"
    mock_config.hosts[0].paths = [path1, path2]
    
    cmd_run(None, mock_config)
    
    session_instance.record_host_status.assert_called_with("testhost", "partial")
