import pytest
from unittest.mock import patch, MagicMock, call
from bekusup.cli import cmd_run, is_host_online

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


@patch('bekusup.cli.subprocess.run')
def test_is_host_online_uses_custom_port(mock_run):
    mock_run.return_value = MagicMock()
    assert is_host_online("ssh://user@example.test:2222") is True
    args, _ = mock_run.call_args
    cmd = args[0]
    assert cmd[:3] == ["nc", "-z", "-w"]
    assert cmd[-2:] == ["example.test", "2222"]


@patch('bekusup.cli.subprocess.run')
def test_is_host_online_defaults_to_22(mock_run):
    mock_run.return_value = MagicMock()
    assert is_host_online("ssh://user@example.test") is True
    args, _ = mock_run.call_args
    assert args[0][-2:] == ["example.test", "22"]


def _make_session_factory(finalize_manifests):
    """Build a SessionManager mock factory that records per-drive state.

    finalize_manifests is mutated so callers can assert what each drive saw.
    """
    sessions = []

    def mk_session(mp, cfg, store, drive_id):
        sm = MagicMock()
        sm.target_mount = mp
        sm.sessions_dir = f"{mp}/sessions"
        sm.timestamp = f"ts-{drive_id}"
        sm.snapshot_base = None
        sm.begin_session.return_value = True
        sm.manifest = {"outcome": "complete", "hosts": {}}

        def get_host_dest(name, _mp=mp, _d=drive_id):
            return f"{_mp}/sessions/ts-{_d}/{name}"
        sm.get_host_dest_dir.side_effect = get_host_dest

        def record(name, status, details=None, _sm=sm):
            _sm.manifest["hosts"][name] = {"status": status, "details": details}
        sm.record_host_status.side_effect = record

        def finalize(_sm=sm, _mp=mp):
            all_ok = all(h["status"] == "succeeded" for h in _sm.manifest["hosts"].values())
            _sm.manifest["outcome"] = "complete" if all_ok else "complete_with_warnings"
            finalize_manifests.append((_mp, dict(_sm.manifest)))
        sm.finalize.side_effect = finalize

        sessions.append(sm)
        return sm

    return mk_session, sessions


@patch('bekusup.cli.os.path.exists', return_value=True)
@patch('bekusup.cli.os.makedirs')
@patch('bekusup.cli.is_host_online', return_value=True)
@patch('bekusup.transports.get_provider')
@patch('bekusup.scanner.get_verified_targets')
@patch('bekusup.store.IndexStore')
@patch('bekusup.session.SessionManager')
def test_cmd_run_freshness_ordering(
    mock_session_mgr, mock_index_cls, mock_get_targets,
    mock_get_provider, mock_online, mock_makedirs, mock_exists,
):
    mock_get_targets.return_value = [
        ({"name": "sda"}, "/mnt/A", "serial-A", "uuid-A", "labA"),
        ({"name": "sdb"}, "/mnt/B", "serial-B", "uuid-B", "labB"),
    ]

    store_instance = MagicMock()
    def get_drive(drive_id):
        if drive_id == "serial-A":
            return {"sessions": {"s1": {"outcome": "complete", "timestamp": 100}}}
        if drive_id == "serial-B":
            return {"sessions": {"s1": {"outcome": "complete", "timestamp": 500}}}
        return None
    store_instance.get_drive.side_effect = get_drive
    mock_index_cls.return_value = store_instance

    call_order = []
    def mk(mp, cfg, store, drive_id):
        call_order.append(mp)
        sm = MagicMock()
        sm.begin_session.return_value = False
        return sm
    mock_session_mgr.side_effect = mk

    cfg = MagicMock()
    cfg.hosts = []
    cfg.run_policy.max_parallel_hosts = 1

    cmd_run(None, cfg)

    assert call_order == ["/mnt/B", "/mnt/A"], (
        f"Fresher drive B (ts=500) must run before A (ts=100); got {call_order}"
    )


@patch('bekusup.cli.os.path.exists', return_value=True)
@patch('bekusup.cli.os.makedirs')
@patch('bekusup.cli.is_host_online', return_value=True)
@patch('bekusup.transports.get_provider')
@patch('bekusup.scanner.get_verified_targets')
@patch('bekusup.store.IndexStore')
@patch('bekusup.session.SessionManager')
def test_cmd_run_cross_drive_copy_dest(
    mock_session_mgr, mock_index_cls, mock_get_targets,
    mock_get_provider, mock_online, mock_makedirs, mock_exists,
):
    mock_get_targets.return_value = [
        ({"name": "sda"}, "/mnt/A", "sA", "uA", "labA"),
        ({"name": "sdb"}, "/mnt/B", "sB", "uB", "labB"),
    ]
    mock_index_cls.return_value.get_drive.return_value = None

    finalize_manifests = []
    factory, _ = _make_session_factory(finalize_manifests)
    mock_session_mgr.side_effect = factory

    provider = MagicMock()
    provider.sync.return_value = True
    mock_get_provider.return_value = provider

    host = MagicMock()
    host.name = "h1"
    host.uri = "ssh://10.0.0.1"
    p = MagicMock(); p.source = "/src"; p.dest_subdir = "data"
    host.paths = [p]

    cfg = MagicMock()
    cfg.hosts = [host]
    cfg.run_policy.max_parallel_hosts = 1

    cmd_run(None, cfg)

    sync_calls = provider.sync.call_args_list
    assert len(sync_calls) == 2

    _, kw_a = sync_calls[0]
    assert kw_a["snapshot_base"] is None, "First drive has no snapshot base"

    _, kw_b = sync_calls[1]
    assert kw_b["snapshot_base"] is not None
    assert "/mnt/A/sessions/ts-sA" in kw_b["snapshot_base"]
    assert "/h1/data" in kw_b["snapshot_base"]


@patch('bekusup.cli.os.path.exists', return_value=True)
@patch('bekusup.cli.os.makedirs')
@patch('bekusup.cli.is_host_online', return_value=True)
@patch('bekusup.transports.get_provider')
@patch('bekusup.scanner.get_verified_targets')
@patch('bekusup.store.IndexStore')
@patch('bekusup.session.SessionManager')
def test_cmd_run_partial_a_preserves_b_reuse_for_succeeded_hosts(
    mock_session_mgr, mock_index_cls, mock_get_targets,
    mock_get_provider, mock_online, mock_makedirs, mock_exists,
):
    mock_get_targets.return_value = [
        ({"name": "sda"}, "/mnt/A", "sA", "uA", "labA"),
        ({"name": "sdb"}, "/mnt/B", "sB", "uB", "labB"),
    ]
    mock_index_cls.return_value.get_drive.return_value = None

    finalize_manifests = []
    factory, _ = _make_session_factory(finalize_manifests)
    mock_session_mgr.side_effect = factory

    def fake_sync(source, dest, snapshot_base=None):
        # Drive A: h1 succeeds, h2 fails. Drive B: both succeed.
        if "/mnt/A" in dest:
            return "h1" in dest
        return True

    provider = MagicMock()
    provider.sync.side_effect = fake_sync
    mock_get_provider.return_value = provider

    h1 = MagicMock(); h1.name = "h1"; h1.uri = "ssh://10.0.0.1"
    p1 = MagicMock(); p1.source = "/src1"; p1.dest_subdir = "data"
    h1.paths = [p1]

    h2 = MagicMock(); h2.name = "h2"; h2.uri = "ssh://10.0.0.2"
    p2 = MagicMock(); p2.source = "/src2"; p2.dest_subdir = "data"
    h2.paths = [p2]

    cfg = MagicMock()
    cfg.hosts = [h1, h2]
    cfg.run_policy.max_parallel_hosts = 1

    cmd_run(None, cfg)

    # Confirm A's manifest shows h1 succeeded and h2 failed
    a_manifest = next(m for mp, m in finalize_manifests if mp == "/mnt/A")
    assert a_manifest["hosts"]["h1"]["status"] == "succeeded"
    assert a_manifest["hosts"]["h2"]["status"] == "failed"
    assert a_manifest["outcome"] == "complete_with_warnings"

    # Inspect drive B's sync calls: h1 should get cross-drive base, h2 should not
    b_calls = [
        c for c in provider.sync.call_args_list
        if "/mnt/B" in c.args[1] or "/mnt/B" in c.kwargs.get("dest", "")
    ]
    # Fallback: positional dest is args[1]
    h1_b = next(c for c in b_calls if "/h1/" in c.args[1])
    h2_b = next(c for c in b_calls if "/h2/" in c.args[1])

    assert h1_b.kwargs["snapshot_base"] is not None
    assert "/mnt/A/sessions/ts-sA" in h1_b.kwargs["snapshot_base"]
    assert h2_b.kwargs["snapshot_base"] is None, (
        "h2 failed on A, so B must NOT reuse A as cache for h2"
    )
