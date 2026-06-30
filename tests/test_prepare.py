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
