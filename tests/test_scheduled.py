"""Unit tests for scheduled-check's handling of prepare outcomes."""

import argparse

from mip_channel_tools import scheduled


def _run(tmp_path, monkeypatch, outcomes):
    """Run scheduled-check with probe_pair results given per package."""
    monkeypatch.setattr(
        scheduled, "list_all_packages", lambda root: list(outcomes))
    monkeypatch.setattr(scheduled, "candidate_arches", lambda d: ["any"])
    monkeypatch.setattr(
        scheduled, "probe_pair",
        lambda root, pkg, arch, probe_dir: outcomes[pkg])
    args = argparse.Namespace(
        repo_root=str(tmp_path),
        dispatch_file=str(tmp_path / "dispatch.tsv"),
        summary_file=str(tmp_path / "summary.md"))
    rc = scheduled.run(args)
    return (rc, (tmp_path / "dispatch.tsv").read_text(),
            (tmp_path / "summary.md").read_text())


def test_unreachable_published_source_is_a_warning(tmp_path, monkeypatch):
    rc, dispatch, summary = _run(tmp_path, monkeypatch, {
        "packages/a/1.0": (False, None, False),
        "packages/b/1.0": (True, None, False),
        "packages/mmess/3.1": (False, None, True),
    })
    assert rc == 0
    assert dispatch == "packages/b/1.0\tany\n"
    assert "`packages/mmess/3.1` (`any`) — source unreachable" in summary


def test_prepare_failure_still_fails(tmp_path, monkeypatch):
    rc, dispatch, summary = _run(tmp_path, monkeypatch, {
        "packages/b/1.0": (True, None, False),
        "packages/c/1.0": (False, "prepare exited 1", False),
        "packages/mmess/3.1": (False, None, True),
    })
    assert rc == 1
    assert dispatch == "packages/b/1.0\tany\n"
    assert "prepare failed: prepare exited 1" in summary
