"""Unit tests for the cross-channel submission parser/resolver."""

import textwrap

import pytest

from mip_channel_tools import submit_package_request as sr


# --- title parsing --------------------------------------------------------

def test_parse_basic():
    spec = sr.parse_submit_title("submit mip-org/staging/fmm2d@main")
    assert spec["owner"] == "mip-org"
    assert spec["channel"] == "staging"
    assert spec["name"] == "fmm2d"
    assert spec["release"] == "main"
    assert spec["source_repo"] == "mip-org/mip-staging"
    assert spec["package_path"] == "packages/fmm2d/main"


def test_parse_case_and_whitespace_insensitive():
    spec = sr.parse_submit_title("  Submit   owner/labs/foo_bar@1.2.3  ")
    assert spec["source_repo"] == "owner/mip-labs"
    assert spec["package_path"] == "packages/foo_bar/1.2.3"


@pytest.mark.parametrize("title", [
    "build fmm2d@main linux_x86_64",   # not a submission
    "submit fmm2d@main",               # missing owner/channel
    "submit owner/channel/name",       # missing @release
    "submit owner//name@rel",          # empty channel
    "",
])
def test_parse_rejects_invalid(title):
    assert sr.parse_submit_title(title) is None


# --- architecture resolution ---------------------------------------------

MIP_YAML = textwrap.dedent("""
    name: fmm2d
    builds:
      - architectures: [linux_x86_64, macos_x86_64, macos_arm64]
      - architectures: [windows_x86_64]
      - architectures: [numbl_wasm]
""")


def _spec():
    return sr.parse_submit_title("submit mip-org/staging/fmm2d@main")


def test_remote_arches_intersects_supported(monkeypatch):
    monkeypatch.setattr(sr, "fetch_remote_file", lambda repo, path: MIP_YAML)
    arches, err = sr.remote_arches(_spec())
    assert err is None
    # macos_x86_64 is dropped (unsupported); order follows SUPPORTED list.
    assert arches == [
        "linux_x86_64", "macos_arm64", "windows_x86_64", "numbl_wasm"]


def test_remote_arches_missing_mip_yaml(monkeypatch):
    monkeypatch.setattr(sr, "fetch_remote_file", lambda repo, path: None)
    arches, err = sr.remote_arches(_spec())
    assert arches == []
    assert "not found" in err


def test_remote_arches_no_supported(monkeypatch):
    monkeypatch.setattr(
        sr, "fetch_remote_file",
        lambda repo, path: "name: x\nbuilds:\n  - architectures: [solaris]\n")
    arches, err = sr.remote_arches(_spec())
    assert arches == []
    assert "no supported architecture" in err


# --- resolve subcommand ---------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_cmd_resolve_writes_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/fmm2d@main")
    monkeypatch.setattr(sr, "remote_package_exists", lambda spec: True)
    monkeypatch.setattr(
        sr, "remote_arches",
        lambda spec: (["linux_x86_64", "windows_x86_64"], None))
    dispatch = tmp_path / "d.tsv"
    rc = sr.cmd_resolve(_Args(
        dispatch_file=str(dispatch), errors_file=None, for_promotion=False))
    assert rc == 0
    rows = dispatch.read_text().splitlines()
    assert rows == [
        "packages/fmm2d/main\tlinux_x86_64\tmip-org/mip-staging",
        "packages/fmm2d/main\twindows_x86_64\tmip-org/mip-staging",
    ]


def test_cmd_resolve_for_promotion_blanks_source_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/fmm2d@main")
    monkeypatch.setattr(sr, "remote_package_exists", lambda spec: True)
    monkeypatch.setattr(
        sr, "remote_arches", lambda spec: (["any"], None))
    dispatch = tmp_path / "d.tsv"
    rc = sr.cmd_resolve(_Args(
        dispatch_file=str(dispatch), errors_file=None, for_promotion=True))
    assert rc == 0
    assert dispatch.read_text().splitlines() == ["packages/fmm2d/main\tany\t"]


def test_cmd_resolve_missing_package_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/ghost@main")
    monkeypatch.setattr(sr, "remote_package_exists", lambda spec: False)
    dispatch = tmp_path / "d.tsv"
    errors = tmp_path / "e.md"
    rc = sr.cmd_resolve(_Args(
        dispatch_file=str(dispatch), errors_file=str(errors),
        for_promotion=False))
    assert rc == 1
    assert dispatch.read_text() == ""
    assert "not found" in errors.read_text()


def test_cmd_resolve_invalid_title_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "just some prose")
    dispatch = tmp_path / "d.tsv"
    errors = tmp_path / "e.md"
    rc = sr.cmd_resolve(_Args(
        dispatch_file=str(dispatch), errors_file=str(errors),
        for_promotion=False))
    assert rc == 1
    assert "not a valid submission" in errors.read_text()


# --- spec subcommand ------------------------------------------------------

def test_cmd_spec_writes_key_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/fmm2d@main")
    out = tmp_path / "spec.txt"
    rc = sr.cmd_spec(_Args(output_file=str(out)))
    assert rc == 0
    kv = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert kv["source_repo"] == "mip-org/mip-staging"
    assert kv["package_path"] == "packages/fmm2d/main"
    assert kv["name"] == "fmm2d"


# --- validate comment rendering ------------------------------------------

def test_validate_comment_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/ghost@main")
    monkeypatch.setattr(sr, "remote_package_exists", lambda spec: False)
    out = tmp_path / "c.md"
    sr.cmd_validate(_Args(output_file=str(out), auto_approved=False))
    assert "was not found" in out.read_text()


def test_validate_comment_ok_lists_arches_and_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_TITLE", "submit mip-org/staging/fmm2d@main")
    monkeypatch.setattr(sr, "remote_package_exists", lambda spec: True)
    monkeypatch.setattr(
        sr, "remote_arches", lambda spec: (["linux_x86_64"], None))
    out = tmp_path / "c.md"
    sr.cmd_validate(_Args(output_file=str(out), auto_approved=True))
    body = out.read_text()
    assert "linux_x86_64" in body
    assert "`build`" in body
    assert "`accept`" in body
