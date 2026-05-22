from unittest.mock import MagicMock

from bekusup.config import HostConfig, PathConfig
from bekusup.session import SessionManager


def _config():
    cfg = MagicMock()
    cfg.run_policy.min_free_space_gb = 0
    cfg.run_policy.incomplete_suffix = ".incomplete"
    cfg.run_policy.complete_marker = "SESSION_COMPLETE"
    return cfg


def test_session_uses_flat_user_host_timestamp_layout(tmp_path):
    store = MagicMock()
    host = HostConfig(
        name="via",
        transport="ssh",
        uri="ssh://ian@via",
        paths=[PathConfig(source="/home/ian", dest_subdir=".")],
    )
    session = SessionManager(str(tmp_path), _config(), store, "drive1", dry_run=False)
    session.timestamp = "2026-04-29T13-42-55"

    assert session.begin_session()
    dest = session.get_host_dest_dir(host)

    assert dest == str(tmp_path / "ian@viaT2026-04-29T13-42-55.incomplete")
    session.record_host_status("via", "succeeded")
    session.finalize()

    final_dir = tmp_path / "ian@viaT2026-04-29T13-42-55"
    assert final_dir.is_dir()
    assert (final_dir / "SESSION_COMPLETE").exists()
    assert not (tmp_path / "sessions").exists()


def test_find_snapshot_base_uses_previous_matching_host_folder(tmp_path):
    cfg = _config()
    store = MagicMock()
    host = HostConfig(
        name="via",
        transport="ssh",
        uri="ssh://ian@via",
        paths=[PathConfig(source="/home/ian", dest_subdir=".")],
    )
    older = tmp_path / "ian@viaT2026-04-28T10-00-00"
    newer = tmp_path / "ian@viaT2026-04-29T10-00-00"
    other = tmp_path / "rene@localhostT2026-04-29T11-00-00"
    for path in (older, newer, other):
        path.mkdir()
        (path / "SESSION_COMPLETE").write_text("done")

    session = SessionManager(str(tmp_path), cfg, store, "drive1", dry_run=False)

    assert session.get_snapshot_base_for_host(host) == str(newer)
