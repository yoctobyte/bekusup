import os
from unittest.mock import MagicMock, patch

from bekusup.hardlink_candidates import (
    find_hardlink_candidate_groups,
    fix_candidate_groups,
    resolve_link_mode,
    summarize_groups,
)


def test_find_hardlink_candidates_reports_duplicate_content_not_same_inode(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    unique = tmp_path / "unique"
    already_linked = tmp_path / "already-linked"

    first.write_text("same content")
    second.write_text("same content")
    unique.write_text("different")
    os.link(first, already_linked)

    groups = find_hardlink_candidate_groups(str(tmp_path))

    assert len(groups) == 1
    group = groups[0]
    assert group["inode_count"] == 2
    assert group["path_count"] == 3
    assert group["potential_savings"] == len("same content")
    assert str(first) in group["paths"]
    assert str(second) in group["paths"]
    assert str(already_linked) in group["paths"]

    summary = summarize_groups(groups)
    assert summary["groups"] == 1
    assert summary["potential_savings"] == len("same content")


def test_find_hardlink_candidates_skips_metadata_and_incomplete_dirs(tmp_path):
    complete_a = tmp_path / "hostT1"
    complete_b = tmp_path / "hostT2"
    incomplete = tmp_path / "hostT3.incomplete"
    complete_a.mkdir()
    complete_b.mkdir()
    incomplete.mkdir()

    (complete_a / "manifest.json").write_text("same")
    (complete_b / "manifest.json").write_text("same")
    (incomplete / "data").write_text("same")
    (complete_a / "data").write_text("same")

    groups = find_hardlink_candidate_groups(str(tmp_path))

    assert groups == []


def test_fix_candidate_groups_hardlinks_duplicate_files(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.write_text("same content")
    second.write_text("same content")

    groups = find_hardlink_candidate_groups(str(tmp_path))
    result = fix_candidate_groups(groups, "hardlink")

    assert result.changed == 1
    assert result.errors == 0
    assert os.stat(first).st_ino == os.stat(second).st_ino


def test_fix_candidate_groups_hardlink_skips_metadata_mismatch(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.write_text("same content")
    second.write_text("same content")
    first.chmod(0o600)
    second.chmod(0o644)

    groups = find_hardlink_candidate_groups(str(tmp_path))
    result = fix_candidate_groups(groups, "hardlink")

    assert result.changed == 0
    assert result.skipped == 1
    assert os.stat(first).st_ino != os.stat(second).st_ino


@patch("bekusup.hardlink_candidates.subprocess.run")
def test_resolve_link_mode_auto_prefers_reflink_on_xfs(mock_run):
    mock_run.return_value = MagicMock(stdout="xfs\n")

    assert resolve_link_mode("/mnt/backup", "auto") == "reflink"


@patch("bekusup.hardlink_candidates._replace_with_reflink")
def test_fix_candidate_groups_reflink_uses_reflink_replacement(mock_reflink, tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.write_text("same content")
    second.write_text("same content")

    groups = find_hardlink_candidate_groups(str(tmp_path))
    result = fix_candidate_groups(groups, "reflink")

    assert result.changed == 1
    mock_reflink.assert_called_once_with(str(first), str(second))
