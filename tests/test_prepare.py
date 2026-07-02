"""Unit tests for prepare's source-hash computation."""

import os

from mip_channel_tools.prepare import compute_directory_hash


def _make_tree(root):
    """A tree with nested subdirectories, so relative paths contain a
    separator (the only thing whose encoding differs across platforms)."""
    os.makedirs(os.path.join(root, "matlab", "numbl", "mex_shim"))
    with open(os.path.join(root, "top.txt"), "wb") as f:
        f.write(b"top")
    with open(os.path.join(root, "matlab", "a.m"), "wb") as f:
        f.write(b"a")
    with open(os.path.join(root, "matlab", "numbl", "mex_shim", "mex.h"), "wb") as f:
        f.write(b"mex")


def test_directory_hash_is_path_separator_independent(tmp_path, monkeypatch):
    # A release folder with subdirectories must hash the same on a Windows
    # build runner and the Linux scheduled-build probe. Regression for the
    # daily false rebuild of windows_x86_64 (fmm3d, fmm2d, finufft, ...).
    root = tmp_path / "pkg"
    _make_tree(str(root))
    baseline = compute_directory_hash(str(root))

    # Simulate a Windows runner: os.path.relpath yields '\\'-separated
    # paths and os.sep is '\\'.
    import mip_channel_tools.prepare as prep
    real_relpath = os.path.relpath
    monkeypatch.setattr(
        prep.os.path, "relpath",
        lambda p, s: real_relpath(p, s).replace("/", "\\"))
    monkeypatch.setattr(prep.os, "sep", "\\")

    simulated_windows = compute_directory_hash(str(root))
    assert simulated_windows == baseline


def test_directory_hash_changes_with_content(tmp_path):
    # Sanity: the hash still reflects file content (the normalisation
    # must not flatten distinct trees to the same digest).
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_tree(str(a))
    _make_tree(str(b))
    with open(os.path.join(str(b), "matlab", "a.m"), "wb") as f:
        f.write(b"changed")
    assert compute_directory_hash(str(a)) != compute_directory_hash(str(b))


# --- Channel version rules (mip-org/mip#315) --------------------------------

import pytest

from mip_channel_tools.prepare import (
    is_numeric_version, validate_channel_version_rules)


def _write_mip_yaml(tmp_path, version_line):
    mip_yaml = tmp_path / "mip.yaml"
    mip_yaml.write_text(f"name: mypkg\n{version_line}\n")
    return str(mip_yaml)


def test_is_numeric_version():
    assert is_numeric_version("1")
    assert is_numeric_version("1.2.3")
    assert not is_numeric_version("")
    assert not is_numeric_version("main")
    assert not is_numeric_version("v1.2.3")
    assert not is_numeric_version("1.2.3-beta")
    assert not is_numeric_version("1e3")
    assert not is_numeric_version("1.")


def test_recipe_must_not_carry_version(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    with pytest.raises(ValueError, match="must not contain a 'version'"):
        validate_channel_version_rules(path, {"version": "1.2.3"}, "1.2.3")


def test_mip_yaml_version_must_be_numeric(tmp_path):
    path = _write_mip_yaml(tmp_path, "version: main")
    with pytest.raises(ValueError, match="blank or numeric"):
        validate_channel_version_rules(path, {}, "main")


def test_numeric_release_dir_must_match_yaml_version(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    with pytest.raises(ValueError, match="must equal"):
        validate_channel_version_rules(path, {}, "2.0.0")


def test_numeric_release_dir_matching_yaml_version_ok(tmp_path):
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    validate_channel_version_rules(path, {}, "1.2.3")


def test_non_numeric_release_dir_takes_precedence(tmp_path):
    # A branch-named release dir is allowed regardless of the yaml version
    # and regardless of what source.branch says.
    path = _write_mip_yaml(tmp_path, 'version: "1.2.3"')
    validate_channel_version_rules(
        path, {"source": {"branch": "develop"}}, "main")


def test_blank_yaml_version_allows_any_release_dir(tmp_path):
    path = _write_mip_yaml(tmp_path, "version:")
    validate_channel_version_rules(path, {}, "3.1.4")
    validate_channel_version_rules(path, {}, "master")


def test_unquoted_numeric_yaml_version_ok(tmp_path):
    # YAML coerces an unquoted 1.5 to a float; the rules still apply to
    # its string form.
    path = _write_mip_yaml(tmp_path, "version: 1.5")
    validate_channel_version_rules(path, {}, "1.5")
    with pytest.raises(ValueError, match="must equal"):
        validate_channel_version_rules(path, {}, "1.6")
