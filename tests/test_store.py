import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch
from bekusup.store import IndexStore, user_data_path, verify_trust

def test_index_store_log_session(tmp_path):
    index_file = tmp_path / "index.json"
    store = IndexStore(index_file=str(index_file))
    
    did = store.enroll_drive("123", "uuid1", "label1")
    
    hosts_data = {
        "laptop1": {"status": "succeeded"},
        "server1": {"status": "unreachable"}
    }
    
    store.log_session(did, "session_test_1", "complete_with_warnings", hosts_data)
    
    with open(index_file, "r") as f:
        data = json.load(f)
        
    sess = data["drives"][did]["sessions"]["session_test_1"]
    assert sess["outcome"] == "complete_with_warnings"
    assert "laptop1" in sess["hosts"]
    assert sess["hosts"]["laptop1"]["status"] == "succeeded"


@patch.dict(os.environ, {"SUDO_USER": "rene"})
@patch("bekusup.store.os.geteuid", return_value=0)
@patch("bekusup.store.pwd.getpwnam")
def test_user_data_path_uses_invoking_user_home_under_sudo(mock_getpwnam, mock_geteuid):
    mock_getpwnam.return_value.pw_dir = "/home/rene"

    assert user_data_path("~/.local/share/bekusup/index.json") == Path(
        "/home/rene/.local/share/bekusup/index.json"
    )

def test_verify_trust_missing_marker(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    # Drive not in index
    ok, msg = verify_trust({"serial": "111", "uuid": "222", "label": "L"}, str(tmp_path), store)
    assert not ok
    assert "No marker file found" in msg

def test_verify_trust_index_known_marker_missing(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    store.enroll_drive("111", "222", "L")
    
    ok, msg = verify_trust({"serial": "111", "uuid": "222", "label": "L"}, str(tmp_path), store)
    assert not ok
    assert "missing marker file!" in msg

@patch('bekusup.store.read_marker_file')
def test_verify_trust_marker_present_but_unenrolled(mock_read, tmp_path):
    mock_read.return_value = {"serial": "111", "uuid": "222"}
    store = IndexStore(tmp_path / "index.json")
    
    ok, msg = verify_trust({"serial": "111", "uuid": "222", "label": "L"}, str(tmp_path), store)
    assert not ok
    assert "unrecognized by this machine" in msg

@patch('bekusup.store.read_marker_file')
def test_verify_trust_hard_rejects_spoofed_uuid(mock_read, tmp_path):
    # The disk has marker UUID "222"
    mock_read.return_value = {"serial": "111", "uuid": "222"}
    store = IndexStore(tmp_path / "index.json")
    store.enroll_drive("111", "222", "L")
    
    # BUT lsblk physical node returns UUID "999" (spoofing)
    ok, msg = verify_trust({"serial": "111", "uuid": "999", "label": "L"}, str(tmp_path), store)
    assert not ok
    assert "HARD REJECT" in msg
    assert "Marker UUID '222' conflicts with actual UUID '999'" in msg

@patch('bekusup.store.read_marker_file')
def test_verify_trust_success(mock_read, tmp_path):
    mock_read.return_value = {"serial": "111", "uuid": "222"}
    store = IndexStore(tmp_path / "index.json")
    store.enroll_drive("111", "222", "L")
    
    ok, msg = verify_trust({"serial": "111", "uuid": "222", "label": "L"}, str(tmp_path), store)
    assert ok

def test_enroll_drive_empty_ids(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    with pytest.raises(ValueError, match="Hardware returned no Serial or UUID"):
        store.enroll_drive("", "", "L")

def test_get_last_success_for_host(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    did = store.enroll_drive("123", "uuid1", "label1")
    
    hosts_data = {"hostA": {"status": "succeeded"}}
    store.log_session(did, "sess1", "complete", hosts_data)
    
    # Needs a mock timestamp essentially, log_session writes timestamp
    d_id, s_id, t = store.get_last_success_for_host("hostA")
    assert d_id == did
    assert s_id == "sess1"


def test_get_last_success_for_host_picks_newest_across_drives(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    did_a = store.enroll_drive("A-serial", "A-uuid", "A")
    did_b = store.enroll_drive("B-serial", "B-uuid", "B")

    # Drive A backed up hostX at t=100, drive B backed up hostX at t=200
    with patch('bekusup.store.time.time', return_value=100):
        store.log_session(did_a, "sess_a", "complete", {"hostX": {"status": "succeeded"}})
    with patch('bekusup.store.time.time', return_value=200):
        store.log_session(did_b, "sess_b", "complete", {"hostX": {"status": "succeeded"}})

    d_id, s_id, ts = store.get_last_success_for_host("hostX")
    assert d_id == did_b
    assert s_id == "sess_b"
    assert ts == 200


def test_drives_with_successful_host(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    did_a = store.enroll_drive("A-serial", "A-uuid", "A")
    did_b = store.enroll_drive("B-serial", "B-uuid", "B")
    did_c = store.enroll_drive("C-serial", "C-uuid", "C")

    store.log_session(did_a, "s1", "complete", {"hostX": {"status": "succeeded"}})
    store.log_session(did_b, "s1", "complete_with_warnings", {"hostX": {"status": "partial"}})
    store.log_session(did_c, "s1", "complete_with_warnings", {"hostX": {"status": "failed"}})

    drives = store.drives_with_successful_host("hostX")
    assert set(drives) == {did_a, did_b}
    assert did_c not in drives


def test_most_recent_drive_for_host(tmp_path):
    store = IndexStore(tmp_path / "index.json")
    did_a = store.enroll_drive("A-serial", "A-uuid", "A")
    did_b = store.enroll_drive("B-serial", "B-uuid", "B")

    with patch('bekusup.store.time.time', return_value=50):
        store.log_session(did_a, "s1", "complete", {"hostY": {"status": "succeeded"}})
    with patch('bekusup.store.time.time', return_value=300):
        store.log_session(did_b, "s1", "complete", {"hostY": {"status": "succeeded"}})

    assert store.most_recent_drive_for_host("hostY") == did_b
    assert store.most_recent_drive_for_host("nobody") is None
